"""Builds a `FeatureContext` from the DB for one (underlying, as_of) — the
only file in `kairodex.features` that touches a session, deliberately
separate from `compute/*.py` so "is the math right" (DB-free, tested
locally) and "did we fetch the right rows" (needs a live DB, verified on
the VM per CLAUDE.md) stay two independently-answerable questions.

`index_bars` is left for the caller (`dataclasses.replace`) — "which index
is the benchmark" is the caller's decision. `session_open_ts` and
`iv_history` are set here since 2026-09-15: leaving them to callers meant no
caller ever set them, and three features were MISSING on every row.
"""

from __future__ import annotations

import dataclasses
import datetime
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kairodex.core.enums import InstrumentKind, Market, Segment
from kairodex.core.sessions import local_date_for, session_window_utc
from kairodex.data.types import Bar, ChainSnapshot, Tick, Timeframe
from kairodex.features import iv_history as iv_history_module
from kairodex.features.types import FeatureContext
from kairodex.store.models import Instrument, OptionQuote, UnderlyingBar

_DEFAULT_BARS_LOOKBACK_DAYS = 5
_DEFAULT_MAX_EXPIRIES = 2  # matches T1 REST poll's own scope (recorder.py's poll_chain_once)
# How far back `load_chain` will look for each leg's latest quote.
#
# Without a lower bound the DISTINCT ON below scans *every* historical
# option_quotes row for every leg, so the query got monotonically slower
# as the hypertable grew and eventually ate the trading day: measured on
# the VM 2026-09-10 against 36 GB / 22 chunks, one RELIANCE chain read
# did not finish in 75s (production pg_stat_activity showed the same
# query active for 13-28 minutes), and build_context issues two of them
# per underlying per tick. A 22-name sweep the engine assumes takes
# ~150s was taking 4-5 hours, i.e. one cycle per session — which is what
# collapsed nse_stock from 611 signals/day (08-31) to 3 (09-10) and left
# every one of them stamped with the cycle-start clock. Same query
# bounded: 1 hour -> 7.3s, 15 minutes -> 242ms.
#
# 15 minutes is strictly more generous than the entry path's own
# tolerance (live_loop.ENTRY_MAX_QUOTE_AGE_MS is 90s) and 15x the T1
# REST poll cadence, so a leg dropped by this bound was already too
# stale for `compute_fill` to price. Relative to `as_of`, so the
# backtest/backfill path stays a real point-in-time read.
_CHAIN_QUOTE_LOOKBACK = datetime.timedelta(minutes=15)

# relative_strength_vs_index's benchmark, per segment — ARCHITECTURE.md
# doesn't name one explicitly ("which index is the benchmark for this
# underlying" is called out in this module's own docstring as a real
# decision left to the caller); NIFTY 50 for both NSE segments, SPY (the
# broad-market proxy, already tracked as one of the four US_INDEX
# constituents per ADR 0007) for both US segments. First-pass, documented,
# freely revisitable.
#
# "NIFTY", not "Nifty 50": the 2026-08-20 instrument merge renamed the row,
# the old string stopped matching, and `relative_strength` was absent from
# every one of 3,655 signals for 26 days with no error anywhere. Which is
# why a missing benchmark now raises instead of returning [].
_BENCHMARK_SYMBOL: dict[Segment, str] = {
    Segment.NSE_STOCK: "NIFTY",
    Segment.NSE_INDEX: "NIFTY",
    Segment.US_STOCK: "SPY",
    Segment.US_INDEX: "SPY",
}


def benchmark_symbol(segment: Segment) -> str:
    """`segment`'s benchmark index symbol — see `_BENCHMARK_SYMBOL`. The
    public accessor, so `kairodex.backtest.runner` (which needs the same
    benchmark choice `load_index_bars` uses, but resolves the instrument
    itself for a whole date range rather than one `as_of`) doesn't have to
    reach into a private module-level dict."""
    return _BENCHMARK_SYMBOL[segment]


async def load_underlying_bars(
    session: AsyncSession,
    instrument_id: int,
    as_of: datetime.datetime,
    *,
    lookback_days: int = _DEFAULT_BARS_LOOKBACK_DAYS,
    timeframe: Timeframe = Timeframe.ONE_MIN,
) -> list[Bar]:
    rows = await session.scalars(
        select(UnderlyingBar)
        .where(
            UnderlyingBar.instrument_id == instrument_id,
            UnderlyingBar.timeframe == timeframe.value,
            UnderlyingBar.ts <= as_of,
            UnderlyingBar.ts >= as_of - datetime.timedelta(days=lookback_days),
        )
        .order_by(UnderlyingBar.ts)
    )
    return [
        Bar(ts=r.ts, open=r.open, high=r.high, low=r.low, close=r.close, volume=r.volume)
        for r in rows
    ]


async def load_index_bars(
    session: AsyncSession,
    segment: Segment,
    as_of: datetime.datetime,
    *,
    lookback_days: int = _DEFAULT_BARS_LOOKBACK_DAYS,
    timeframe: Timeframe = Timeframe.ONE_MIN,
) -> list[Bar]:
    """Bars for `segment`'s benchmark (`_BENCHMARK_SYMBOL`) — the other
    half `relative_strength_vs_index` needs and nothing was ever supplying
    (verified live: neither `orchestrator.run_entry_tick` nor any backtest
    code populated `FeatureContext.index_bars`, which defaults to `[]`,
    which makes `relative_strength_detector` return `None` on every single
    call — one of only two detector families that can ever fire without
    an option chain, permanently dead).

    Raises `LookupError` if the benchmark instrument does not exist. It used
    to return `[]`, and that polite degradation is exactly how a renamed
    instrument killed the detector for 26 days unnoticed. `[]` now only
    means "the benchmark exists but has no bars in the window"."""
    symbol = _BENCHMARK_SYMBOL[segment]
    exchange = "NSE" if segment.market is Market.NSE else "US"
    benchmark = await session.scalar(
        select(Instrument).where(Instrument.exchange == exchange, Instrument.symbol == symbol)
    )
    if benchmark is None:
        raise LookupError(f"benchmark instrument {symbol!r} not found on {exchange}")
    return await load_underlying_bars(
        session, benchmark.instrument_id, as_of, lookback_days=lookback_days, timeframe=timeframe
    )


async def load_chain(
    session: AsyncSession,
    *,
    exchange: str,
    underlying_symbol: str,
    as_of: datetime.datetime,
    max_expiries: int = _DEFAULT_MAX_EXPIRIES,
) -> list[ChainSnapshot]:
    """One `ChainSnapshot` per expiry, each leg the *latest* option_quotes
    row at or before `as_of` (a real point-in-time read — never a row
    whose `ts` is after `as_of`, which is exactly the lookahead bug
    `FeatureContext`'s docstring warns feature functions can't police
    themselves). Matched by `underlying_symbol` string, not
    `Instrument.underlying_id` — that FK column exists but nothing
    populates it yet (a real, separate gap; see recorder.py's
    `_resolve_ws_keys`, which works around the same thing the same way)."""
    expiry_rows = await session.scalars(
        select(Instrument.expiry)
        .where(
            Instrument.exchange == exchange,
            Instrument.underlying_symbol == underlying_symbol,
            Instrument.kind == InstrumentKind.OPTION,
            Instrument.expiry.is_not(None),
            Instrument.expiry >= as_of.date(),
        )
        .distinct()
        .order_by(Instrument.expiry)
        .limit(max_expiries)
    )
    expiries = list(expiry_rows)
    if not expiries:
        return []

    latest_per_instrument = (
        select(
            OptionQuote,
            Instrument.strike,
            Instrument.option_type,
            Instrument.expiry,
            Instrument.provider_ids,
        )
        .join(Instrument, Instrument.instrument_id == OptionQuote.instrument_id)
        .where(
            Instrument.exchange == exchange,
            Instrument.underlying_symbol == underlying_symbol,
            Instrument.expiry.in_(expiries),
            OptionQuote.ts <= as_of,
            OptionQuote.ts >= as_of - _CHAIN_QUOTE_LOOKBACK,
        )
        .distinct(OptionQuote.instrument_id)
        .order_by(OptionQuote.instrument_id, OptionQuote.ts.desc())
    )
    rows = (await session.execute(latest_per_instrument)).all()

    by_expiry: dict[datetime.date, list[Tick]] = defaultdict(list)
    for oq, strike, option_type, expiry, provider_ids in rows:
        key = next(iter(provider_ids.values())) if provider_ids else str(oq.instrument_id)
        by_expiry[expiry].append(
            Tick(
                instrument_key=str(key),
                ts=oq.ts,
                strike=strike,
                option_type=option_type,
                ltp=oq.ltp,
                bid=oq.bid,
                ask=oq.ask,
                bid_sz=oq.bid_sz,
                ask_sz=oq.ask_sz,
                volume=oq.volume,
                oi=oq.oi,
                oi_change=oq.oi_change,
                underlying_px=oq.underlying_px,
                iv=oq.iv,
                delta=oq.delta,
                gamma=oq.gamma,
                theta=oq.theta,
                vega=oq.vega,
                rho=oq.rho,
                vendor_iv=oq.vendor_iv,
            )
        )

    return [
        ChainSnapshot(underlying=underlying_symbol, expiry=expiry, ts=as_of, quotes=legs)
        for expiry, legs in by_expiry.items()
    ]


async def build_context(
    session: AsyncSession,
    *,
    segment: Segment,
    underlying: Instrument,
    as_of: datetime.datetime,
    prior_as_of: datetime.datetime | None = None,
    bars_lookback_days: int = _DEFAULT_BARS_LOOKBACK_DAYS,
    max_expiries: int = _DEFAULT_MAX_EXPIRIES,
) -> FeatureContext:
    """The common case: bars + current chain (+ prior chain, for
    `oi_change`, if `prior_as_of` is given — typically "the last
    evaluation tick," the caller's to track, not this loader's)."""
    bars = await load_underlying_bars(
        session, underlying.instrument_id, as_of, lookback_days=bars_lookback_days
    )
    if underlying.kind is InstrumentKind.INDEX:
        future = await near_month_future(session, underlying, as_of)
        if future is not None:
            bars = with_proxy_volume(
                bars,
                await load_underlying_bars(
                    session, future.instrument_id, as_of, lookback_days=bars_lookback_days
                ),
            )
    chain = await load_chain(
        session,
        exchange=underlying.exchange,
        underlying_symbol=underlying.symbol,
        as_of=as_of,
        max_expiries=max_expiries,
    )
    prior_chain: list[ChainSnapshot] = []
    if prior_as_of is not None:
        prior_chain = await load_chain(
            session,
            exchange=underlying.exchange,
            underlying_symbol=underlying.symbol,
            as_of=prior_as_of,
            max_expiries=max_expiries,
        )
    local_day = local_date_for(segment.market, as_of)
    # Set from 2026-09-15 (REGISTRY_VERSION "2"). Before, it was "left for
    # the caller" and no caller set it, so `opening_range_position` was
    # MISSING on every row and `vwap_position`/`price_acceptance`/POC
    # silently used all 5 days of loaded bars instead of the session.
    session_open_ts, _ = session_window_utc(segment.market, local_day)
    iv_history = iv_history_module.with_current(
        await iv_history_module.load_prior_iv(session, underlying.instrument_id, local_day),
        iv_history_module.current_atm_iv(chain, local_day),
        as_of,
    )
    return FeatureContext(
        as_of=as_of,
        segment=segment,
        underlying_bars=bars,
        chain=chain,
        prior_chain=prior_chain,
        iv_history=iv_history,
        session_open_ts=session_open_ts,
    )


async def near_month_future(
    session: AsyncSession, underlying: Instrument, as_of: datetime.datetime
) -> Instrument | None:
    """The nearest unexpired future on an index — the traded instrument
    whose volume stands in for the index's own (see `with_proxy_volume`).
    Also used by the recorder to decide which future's bars to fetch."""
    today = as_of.astimezone(datetime.UTC).date()
    row: Instrument | None = await session.scalar(
        select(Instrument)
        .where(
            Instrument.exchange == underlying.exchange,
            Instrument.kind == InstrumentKind.FUTURE,
            Instrument.underlying_symbol == underlying.symbol,
            Instrument.expiry >= today,
        )
        .order_by(Instrument.expiry)
        .limit(1)
    )
    return row


def with_proxy_volume(bars: list[Bar], proxy_bars: list[Bar]) -> list[Bar]:
    """Index bars with each minute's volume taken from the same minute of
    `proxy_bars` (the near-month future). Prices are untouched.

    An index is a calculation, not a traded instrument: NIFTY and BANKNIFTY
    1m bars carry volume 0, so `vwap_position` and `price_acceptance` were
    MISSING on 100% of nse_index feature vectors (2026-09-15) — the
    segment best placed to use the one feature that ever beat its session
    baseline in 6 of 6 sessions (§21c) could not compute it. Minutes the
    future has no bar for keep volume 0 rather than inventing one."""
    if not proxy_bars:
        return bars
    volume_at = {b.ts: b.volume for b in proxy_bars}
    return [
        dataclasses.replace(b, volume=volume_at[b.ts]) if b.ts in volume_at else b for b in bars
    ]
