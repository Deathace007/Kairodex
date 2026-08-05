"""Builds a `FeatureContext` from the DB for one (underlying, as_of) — the
only file in `kairodex.features` that touches a session, deliberately
separate from `compute/*.py` so "is the math right" (DB-free, tested
locally) and "did we fetch the right rows" (needs a live DB, verified on
the VM per CLAUDE.md) stay two independently-answerable questions.

`index_bars`/`iv_history`/`session_open_ts` are left for the caller to
fill in afterward (`dataclasses.replace`) rather than auto-derived here —
"which index is the benchmark for this underlying" and "where does
historical ATM IV come from" are real decisions this loader shouldn't
make silently on a caller's behalf.
"""

from __future__ import annotations

import datetime
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kairodex.core.enums import InstrumentKind, Market, Segment
from kairodex.data.types import Bar, ChainSnapshot, Tick, Timeframe
from kairodex.features.types import FeatureContext
from kairodex.store.models import Instrument, OptionQuote, UnderlyingBar

_DEFAULT_BARS_LOOKBACK_DAYS = 5
_DEFAULT_MAX_EXPIRIES = 2  # matches T1 REST poll's own scope (recorder.py's poll_chain_once)

# relative_strength_vs_index's benchmark, per segment — ARCHITECTURE.md
# doesn't name one explicitly ("which index is the benchmark for this
# underlying" is called out in this module's own docstring as a real
# decision left to the caller); NIFTY 50 for both NSE segments, SPY (the
# broad-market proxy, already tracked as one of the four US_INDEX
# constituents per ADR 0007) for both US segments. First-pass, documented,
# freely revisitable.
_BENCHMARK_SYMBOL: dict[Segment, str] = {
    Segment.NSE_STOCK: "Nifty 50",
    Segment.NSE_INDEX: "Nifty 50",
    Segment.US_STOCK: "SPY",
    Segment.US_INDEX: "SPY",
}


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
    an option chain, permanently dead). Returns `[]`, not an error, if the
    benchmark instrument isn't in `instruments` yet — a missing benchmark
    degrades `relative_strength_vs_index` to `None`, same as any other
    missing feature input, rather than crashing the caller."""
    symbol = _BENCHMARK_SYMBOL[segment]
    exchange = "NSE" if segment.market is Market.NSE else "US"
    benchmark = await session.scalar(
        select(Instrument).where(Instrument.exchange == exchange, Instrument.symbol == symbol)
    )
    if benchmark is None:
        return []
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
    return FeatureContext(
        as_of=as_of, segment=segment, underlying_bars=bars, chain=chain, prior_chain=prior_chain
    )
