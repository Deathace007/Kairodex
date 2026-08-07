"""P1's tiered ingest loop (ARCHITECTURE.md §6, §7): `kairodex ingest run`.

One process per market, matching the process topology in ARCHITECTURE.md
§3 (`ingest --market nse`, `ingest --market us`). Two concurrent tasks:

  - T1 REST poll: every 60s, pull the full chain (nearest 2 expiries) for
    every watchlist underlying via `client.chain()`, quality-flag, upsert.
    This is also what keeps `instruments` current with the live option
    universe — the WS task reads that universe to know what to subscribe to.
  - WS stream: subscribe to every watchlist instrument's live feed,
    quality-flag each tick against the previous tick for that instrument,
    batch-write.

Both update `feed_health` so `kairodex status` has something to read.

Restart recovery (ARCHITECTURE.md §7): on startup, backfill missing
`underlying_bars` since the last recorded bar via REST. Option-quote history
can't be backfilled — Upstox/LSE sell no historical option-chain data (ADR
0002) — recovery for quotes just means resuming polling/streaming, which
the loop does by running.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime
import logging

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from kairodex.core.enums import InstrumentKind, Market, Segment
from kairodex.core.errors import RateLimitError
from kairodex.core.sessions import is_session_open_now, local_date_for
from kairodex.data.factory import make_client
from kairodex.data.ingest import (
    option_quote_row,
    store_chain_snapshot,
    write_option_quotes_batch,
    write_underlying_bars_batch,
)
from kairodex.data.ports import MarketDataProvider
from kairodex.data.quality import flag_tick
from kairodex.data.types import FeedMode, InstrumentRecord, Tick, Timeframe
from kairodex.store.base import get_sessionmaker
from kairodex.store.models import FeedHealth, Instrument, UnderlyingBar, WatchlistMembership

logger = logging.getLogger(__name__)

T1_POLL_INTERVAL = datetime.timedelta(seconds=60)
# LSE meters hard (15000 requests/day) where Upstox does not, so the US poll
# has to be budgeted rather than merely tidied. With the session gate and the
# per-day expiry cache below, a cycle costs `underlyings x 2 expiries + 1`
# calls, and a US session is 6.5h:
#
#     22 underlyings -> 45 calls/cycle
#      60s ->  390 cycles ->  17,572/day  OVER the 15000 cap
#     120s ->  195 cycles ->   8,797/day  ~59% of cap
#
# 120s keeps real headroom for restarts (each re-runs bar backfill), the
# SPY/QQQ probe path, and a watchlist that grows. Chain snapshots for US are
# therefore 2 minutes apart — a real, vendor-imposed resolution limit worth
# knowing before reading US features. Re-derive this before adding
# underlyings; the cap is per-account, not per-symbol.
T1_POLL_INTERVAL_BY_MARKET = {Market.US: datetime.timedelta(seconds=120)}
# How long to idle between checks while the market is closed. The T1 poll has
# nothing to record then — an option chain does not move — and against LSE's
# 15000/day cap those calls are not merely wasted but actively harmful: US
# options trade 32.5 of every 168 hours, so polling around the clock spent
# ~81% of a day's entire quota on a closed book (live, 2026-08-06).
CLOSED_MARKET_POLL_INTERVAL = datetime.timedelta(minutes=5)
# Backoff after a vendor quota rejection. LSE meters daily AND on a rolling
# 7-day window, so retrying into a 429 does not just fail — it deepens the
# weekly debt and delays recovery past the next daily reset. Long enough that
# a blown day costs a handful of probe calls, not tens of thousands.
QUOTA_BACKOFF = datetime.timedelta(minutes=30)
# How stale `underlying_bars` may get before `t1_poll_loop` pulls them
# forward again. Costs one metered call per underlying per refresh, so it is
# budgeted against LSE's cap exactly like the poll interval above:
#
#     22 underlyings x (390 session min / 10 min) = 858 calls/day
#     8,797 (chain poll) + 858 = 9,655/day, ~64% of the 15000 cap
#
# 10 min rather than 1 min because the features reading these bars are EMA
# spreads and 5-day cumulative returns — they move on the scale of tens of
# minutes, not of a single bar — and because the headroom that absorbs
# restarts and watchlist growth is the thing that must not be spent.
BAR_REFRESH_INTERVAL = datetime.timedelta(minutes=10)

# WS reconnect ceilings. A dropped connection is usually transient, so the
# normal cap stays short. A 401/403 on the *handshake* is not transient —
# it is the vendor refusing this token a feed — and retrying it every
# minute neither fixes it nor goes unnoticed at the vendor's end. Live
# 2026-08-07 Upstox refused 327 consecutive handshakes over five hours
# while the very same token kept serving REST market data with HTTP 200,
# so the retries were pure noise against a decision already made upstream.
# Backing off costs tick cadence, not data: the T1 REST poll keeps
# recording the same contracts throughout.
_WS_RECONNECT_CAP = 60.0
_WS_AUTH_REJECT_CAP = 900.0  # 15 min


def _is_auth_rejection(exc: Exception) -> bool:
    """Matched on the HTTP code in the message rather than on
    `websockets.InvalidStatus`, whose module path and name have both moved
    between websockets major versions — the code is the stable part."""
    text = str(exc)
    return "HTTP 401" in text or "HTTP 403" in text
WS_FLUSH_INTERVAL = datetime.timedelta(seconds=3)
WS_FLUSH_SIZE = 200
BACKFILL_LOOKBACK_DAYS = 5  # how far back to search for the last recorded bar on a cold start

# Upstox's WS "full" mode (full_d5 — depth + greeks together, what QUOTE/FULL
# map to in feed.py) silently accepts an oversized `instrumentKeys` subscribe
# list and then never sends a single message — no error, no rejection frame,
# just a connection that looks "connected" forever and streams nothing.
# Live-verified 2026-08-05: 2000 keys streamed real ticks within seconds,
# 3000 produced zero ticks in 25s. Capped here rather than documented as a
# limit callers must respect, since the live watchlist's full future-expiry
# universe (~7,400 keys for 22 NSE underlyings) blows past it by 3-4x.
MAX_WS_SUBSCRIBE_KEYS = 2000


def _provider_key(instrument: Instrument, provider: str) -> str | None:
    """`provider_ids` is JSONB (`dict[str, object]`) since it can hold
    non-string vendor ids in principle — every adapter in this codebase only
    ever puts strings in it, so this is a narrowing cast, not a real check."""
    value = (instrument.provider_ids or {}).get(provider)
    return str(value) if value is not None else None


async def watchlist_instruments(session: AsyncSession, segment: Segment) -> list[Instrument]:
    today = datetime.date.today()
    rows = await session.scalars(
        select(Instrument)
        .join(WatchlistMembership, WatchlistMembership.instrument_id == Instrument.instrument_id)
        .where(
            WatchlistMembership.segment == segment,
            WatchlistMembership.valid_from <= today,
            WatchlistMembership.valid_to >= today,
        )
    )
    return list(rows)


async def update_feed_health(session: AsyncSession, provider: str, **fields: object) -> None:
    now = datetime.datetime.now(datetime.UTC)
    row = await session.get(FeedHealth, provider)
    if row is None:
        row = FeedHealth(provider=provider, connected=False, subscribed_count=0, updated_at=now)
        session.add(row)
    for key, value in fields.items():
        setattr(row, key, value)
    row.updated_at = now
    await session.commit()


# --- Restart recovery (underlying bars only — see module docstring) --------


async def recover_underlying_bars(
    session: AsyncSession,
    client: MarketDataProvider,
    provider: str,
    market: Market,
    underlyings: list[Instrument],
    *,
    max_age: datetime.timedelta = BAR_REFRESH_INTERVAL,
) -> None:
    """Pull `underlying_bars` forward to now for every underlying whose
    newest bar is older than `max_age`.

    Called at startup *and* on a timer from `t1_poll_loop` — which it was
    not, and that was a live fault, not a tuning choice. The old guard
    (`if start >= today: continue`, against a startup-only call site) meant
    the newest bar a running process ever saw was whatever existed the
    moment it booted. Measured live 2026-08-07: with the recorders started
    at 10:15 UTC, the freshest US bar was still 2026-08-06 23:59 four hours
    into the US session, and the freshest NSE bar 2026-08-06 09:59. Every
    bar-derived feature was therefore constant for the whole session —
    `trend_state_strength` (and so the STRUCTURE detector family) returned
    one frozen value per underlying per process lifetime, confirmed by
    every one of the 20 US and 12 NSE underlyings that emitted signals that
    day having exactly one distinct `trend_structure` score in the evidence
    log while `iv_skew_sentiment`, which reads the chain the T1 poll keeps
    current, moved normally. A frozen detector is not a quiet one: it kept
    voting, at a stale magnitude near zero, and dragged the confluence
    average down with it.

    `local_date_for` rather than `date.today()`: the process clock is IST
    on the VM while US bars are timestamped UTC and the US session runs
    past midnight IST, so the two disagreed about which day it was for
    exactly the market that needed the refresh most.

    Backfill is idempotent (`write_underlying_bars_batch` upserts) and
    resumable — it re-derives `start` from the last stored bar — so a
    skipped or failed pass costs nothing the next one won't pick up."""
    now = datetime.datetime.now(datetime.UTC)
    today = local_date_for(market, now)
    for u in underlyings:
        vendor_key = _provider_key(u, provider)
        if vendor_key is None:
            continue
        last_ts = await session.scalar(
            select(func.max(UnderlyingBar.ts)).where(
                UnderlyingBar.instrument_id == u.instrument_id,
                UnderlyingBar.timeframe == Timeframe.ONE_MIN.value,
            )
        )
        if last_ts is not None and now - last_ts < max_age:
            continue
        lookback = datetime.timedelta(days=BACKFILL_LOOKBACK_DAYS)
        start = local_date_for(market, last_ts) if last_ts else today - lookback
        try:
            bars = await client.bars(vendor_key, Timeframe.ONE_MIN, start, today)
        except RateLimitError as e:
            # Same account-level fact as in t1_poll_loop: pushing on would
            # spend one doomed call per remaining underlying on every
            # restart, and a crash-looping unit turns that into thousands.
            # Backfill is resumable by design (it re-derives `start` from
            # the last stored bar), so abandoning the pass costs nothing
            # the next healthy startup won't pick up.
            logger.warning("%s quota exhausted — skipping bar backfill: %s", provider, e)
            return
        except Exception:
            logger.exception("restart-recovery bar backfill failed for %s", u.symbol)
            continue
        n = await write_underlying_bars_batch(
            session, u.instrument_id, Timeframe.ONE_MIN.value, provider, bars
        )
        if n:
            logger.info("backfilled %d 1m bars for %s (%s -> today)", n, u.symbol, start)


# --- T1: REST chain poll ----------------------------------------------------


async def _expiries_cached(
    client: MarketDataProvider,
    vendor_key: str,
    cache: dict[str, tuple[datetime.date, list[datetime.date]]],
) -> list[datetime.date]:
    """`list_expiries` costs a full unfiltered chain fetch (and for SPY/QQQ,
    up to 14 more probe calls) to learn a set of dates that changes about
    weekly. Re-asking every 60s was a third of the entire LSE daily budget
    spent rediscovering a near-static fact. Cached per calendar day: an
    expiry that lists intraday is picked up next day, and the nearest-2 the
    caller actually uses are already known well before then."""
    today = datetime.date.today()
    hit = cache.get(vendor_key)
    if hit is not None and hit[0] == today:
        return hit[1]
    expiries = await client.list_expiries(vendor_key)
    cache[vendor_key] = (today, expiries)
    return expiries


async def poll_chain_once(
    session: AsyncSession,
    client: MarketDataProvider,
    provider: str,
    segment: Segment,
    underlying: Instrument,
    expiry_cache: dict[str, tuple[datetime.date, list[datetime.date]]],
) -> None:
    vendor_key = _provider_key(underlying, provider)
    if vendor_key is None:
        return
    expiries = await _expiries_cached(client, vendor_key, expiry_cache)
    underlying_rec = InstrumentRecord(
        exchange=underlying.exchange,
        symbol=underlying.symbol,
        kind=underlying.kind,
        currency=underlying.currency,
        provider_ids={provider: vendor_key},
    )
    for expiry in expiries[:2]:
        snapshot = await client.chain(vendor_key, expiry)
        await store_chain_snapshot(session, provider, snapshot, underlying_rec, segment)


async def t1_poll_loop(
    sessionmaker: async_sessionmaker[AsyncSession],
    client: MarketDataProvider,
    provider: str,
    market: Market,
    underlyings_by_segment: dict[Segment, list[Instrument]],
) -> None:
    expiry_cache: dict[str, tuple[datetime.date, list[datetime.date]]] = {}
    interval = T1_POLL_INTERVAL_BY_MARKET.get(market, T1_POLL_INTERVAL)
    while True:
        cycle_start = datetime.datetime.now(datetime.UTC)

        if not is_session_open_now(market, cycle_start):
            await asyncio.sleep(CLOSED_MARKET_POLL_INTERVAL.total_seconds())
            continue

        # Keep `underlying_bars` moving during the session, not just at
        # startup — see `recover_underlying_bars`. Inside the session gate
        # so a closed book costs no metered calls, and before the chain
        # poll so a quota abort below doesn't starve bars specifically.
        try:
            async with sessionmaker() as session:
                await recover_underlying_bars(
                    session,
                    client,
                    provider,
                    market,
                    [u for us in underlyings_by_segment.values() for u in us],
                )
        except Exception:
            logger.exception("bar refresh failed for %s", provider)

        # A quota rejection is about the *account*, not this underlying, so
        # it aborts the whole cycle: continuing the loop would issue one
        # doomed call per remaining underlying, every cycle, forever. That
        # is precisely how a blown daily cap became a blown weekly one.
        rate_limited = False
        for segment, underlyings in underlyings_by_segment.items():
            if rate_limited:
                break
            for u in underlyings:
                try:
                    async with sessionmaker() as session:
                        await poll_chain_once(
                            session, client, provider, segment, u, expiry_cache
                        )
                except RateLimitError as e:
                    logger.warning(
                        "%s quota exhausted (%s) — pausing T1 poll for %.0f min",
                        provider,
                        e,
                        QUOTA_BACKOFF.total_seconds() / 60,
                    )
                    async with sessionmaker() as session:
                        await update_feed_health(
                            session,
                            provider,
                            quota_used_pct=100.0,
                            last_error=str(e)[:500],
                            last_error_at=datetime.datetime.now(datetime.UTC),
                        )
                    rate_limited = True
                    break
                except Exception:
                    logger.exception("T1 poll failed for %s (%s)", u.symbol, segment.value)

        if rate_limited:
            await asyncio.sleep(QUOTA_BACKOFF.total_seconds())
            continue

        try:
            quota = await client.quota()
            async with sessionmaker() as session:
                await update_feed_health(session, provider, quota_used_pct=quota.used_pct)
        except Exception:
            logger.exception("quota check failed for %s", provider)

        elapsed = (datetime.datetime.now(datetime.UTC) - cycle_start).total_seconds()
        await asyncio.sleep(max(0.0, interval.total_seconds() - elapsed))


# --- WS stream ---------------------------------------------------------------


async def _resolve_ws_keys(
    session: AsyncSession, provider: str, underlyings: list[Instrument]
) -> list[str]:
    """Upstox subscribes per option-contract instrument key; LSE subscribes
    per underlying (subscribe_options expands to the whole chain server-side)
    — real vendor asymmetry, not a choice. The Upstox leg universe comes from
    `instruments` (kept current by the T1 poll's upserts), so a brand-new
    strike streams from the next reconnect, not instantly — the T1 poll
    still records it every cycle regardless, so nothing is lost.

    Capped at MAX_WS_SUBSCRIBE_KEYS, nearest-expiry legs first — Upstox's
    real subscription ceiling, found live (see that constant's comment).
    Legs beyond the cap still get recorded by the T1 REST poll, just not at
    WS tick cadence."""
    if provider == "lse":
        return [k for u in underlyings if (k := _provider_key(u, provider)) is not None]

    symbols = [u.symbol for u in underlyings]
    if not symbols:
        return []
    rows = await session.scalars(
        select(Instrument)
        .where(
            Instrument.exchange == underlyings[0].exchange,
            Instrument.underlying_symbol.in_(symbols),
            Instrument.kind == InstrumentKind.OPTION,
            Instrument.expiry.is_not(None),
            Instrument.expiry >= datetime.date.today(),
        )
        .order_by(Instrument.expiry)
    )
    keys = [k for r in rows if (k := _provider_key(r, provider)) is not None]
    if len(keys) > MAX_WS_SUBSCRIBE_KEYS:
        logger.warning(
            "%s: %d WS-eligible legs exceeds the %d-key subscription cap, "
            "keeping nearest-expiry %d",
            provider,
            len(keys),
            MAX_WS_SUBSCRIBE_KEYS,
            MAX_WS_SUBSCRIBE_KEYS,
        )
        keys = keys[:MAX_WS_SUBSCRIBE_KEYS]
    return keys


async def _resolve_instrument_id(
    session: AsyncSession, provider: str, key: str, cache: dict[str, int]
) -> int | None:
    if key in cache:
        return cache[key]
    row = await session.scalar(
        select(Instrument).where(Instrument.provider_ids[provider].astext == key)
    )
    if row is None:
        return None
    cache[key] = row.instrument_id
    return row.instrument_id


async def ws_stream_loop(
    sessionmaker: async_sessionmaker[AsyncSession],
    client: MarketDataProvider,
    provider: str,
    underlyings: list[Instrument],
) -> None:
    instrument_ids: dict[str, int] = {}
    prev_ticks: dict[str, Tick] = {}
    buffer: list[dict[str, object]] = []
    reconnect_wait = 1.0

    async def flush(connected: bool) -> None:
        # `buffer[:] = []` after snapshotting is synchronous (no `await`
        # in between), so it can't interleave with the main loop's
        # `buffer.append` — safe without a lock under cooperative
        # scheduling, same reasoning as CPython's GIL for plain lists.
        pending, buffer[:] = list(buffer), []
        async with sessionmaker() as session:
            if pending:
                n = await write_option_quotes_batch(session, pending)
                logger.debug("flushed %d ticks for %s", n, provider)
            await update_feed_health(
                session,
                provider,
                connected=connected,
                last_message_at=datetime.datetime.now(datetime.UTC),
                subscribed_count=len(instrument_ids),
            )

    async def periodic_flush() -> None:
        # Runs as an independent task against its own session, purely so a
        # quiet period doesn't hold ticks in memory indefinitely. Must NOT
        # share a timeout/cancellation path with the tick consumer below:
        # cancelling a pending `anext()` on the WS async generator tears
        # down its `async with websockets.connect(...)` and kills the
        # connection — an earlier version of this loop did exactly that via
        # `asyncio.wait_for(anext(stream), timeout=...)`, reconnecting from
        # scratch every ~3s during any lull instead of just flushing.
        while True:
            await asyncio.sleep(WS_FLUSH_INTERVAL.total_seconds())
            await flush(connected=True)

    while True:
        try:
            async with sessionmaker() as session:
                vendor_keys = await _resolve_ws_keys(session, provider, underlyings)
            if not vendor_keys:
                logger.warning("%s: no WS keys to subscribe (empty watchlist/legs)", provider)
                await asyncio.sleep(T1_POLL_INTERVAL.total_seconds())
                continue

            flush_task = asyncio.create_task(periodic_flush())
            try:
                async for tick in client.subscribe(vendor_keys, FeedMode.FULL):
                    now = datetime.datetime.now(datetime.UTC)
                    prev = prev_ticks.get(tick.instrument_key)
                    # No `expected_interval` here: SEQUENCE_GAP is a
                    # per-instrument tick-to-tick spacing check, but options
                    # (especially thin strikes) can legitimately go minutes
                    # between prints — that's normal market microstructure,
                    # not a feed problem. A live 2s threshold flagged most of
                    # the book as "gapped" during a healthy connection,
                    # which would have made the gap-rate exit criterion
                    # meaningless. Stream-level liveness (did the connection
                    # drop) is what feed_health.connected/last_message_at
                    # already tracks — that's the real gap signal.
                    quality = flag_tick(tick, now=now, prev_tick=prev)
                    prev_ticks[tick.instrument_key] = tick

                    async with sessionmaker() as session:
                        instrument_id = await _resolve_instrument_id(
                            session, provider, tick.instrument_key, instrument_ids
                        )
                    if instrument_id is None:
                        continue  # T1 poll hasn't upserted this contract yet
                    row = option_quote_row(
                        tick, instrument_id, provider=provider, tier=1, quality=quality
                    )
                    buffer.append(row)
                    if len(buffer) >= WS_FLUSH_SIZE:
                        await flush(connected=True)
            finally:
                flush_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await flush_task
                await flush(connected=False)

            # The stream ended cleanly (no exception) — still a short pause
            # before resubscribing, so a persistent rejection (e.g. an
            # expired token) backs off instead of hot-looping the authorize
            # handshake.
            await asyncio.sleep(2.0)
            reconnect_wait = 1.0
        except Exception as e:
            logger.warning(
                "%s WS stream error: %s — reconnecting in %.0fs", provider, e, reconnect_wait
            )
            async with sessionmaker() as session:
                await update_feed_health(
                    session,
                    provider,
                    connected=False,
                    last_error=str(e)[:500],
                    last_error_at=datetime.datetime.now(datetime.UTC),
                )
            await asyncio.sleep(reconnect_wait)
            cap = _WS_AUTH_REJECT_CAP if _is_auth_rejection(e) else _WS_RECONNECT_CAP
            reconnect_wait = min(reconnect_wait * 2, cap)


# --- Entry point --------------------------------------------------------------


async def run_market(market: Market) -> None:
    sessionmaker = get_sessionmaker()
    client, provider = make_client(market)
    segments = [s for s in Segment if s.market is market]

    try:
        async with sessionmaker() as session:
            underlyings_by_segment = {
                segment: await watchlist_instruments(session, segment) for segment in segments
            }
        all_underlyings = [u for us in underlyings_by_segment.values() for u in us]
        if not all_underlyings:
            logger.warning(
                "%s watchlist is empty — run `kairodex ingest sync-instruments` and "
                "`sync-watchlist` first",
                market.value,
            )

        async with sessionmaker() as session:
            await recover_underlying_bars(session, client, provider, market, all_underlyings)

        await asyncio.gather(
            t1_poll_loop(sessionmaker, client, provider, market, underlyings_by_segment),
            ws_stream_loop(sessionmaker, client, provider, all_underlyings),
        )
    finally:
        await client.aclose()
