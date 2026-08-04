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
import datetime
import logging

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from kairodex.core.enums import InstrumentKind, Market, Segment
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
WS_FLUSH_INTERVAL = datetime.timedelta(seconds=3)
WS_FLUSH_SIZE = 200
WS_EXPECTED_TICK_INTERVAL = datetime.timedelta(seconds=2)
BACKFILL_LOOKBACK_DAYS = 5  # how far back to search for the last recorded bar on a cold start


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
    underlyings: list[Instrument],
) -> None:
    today = datetime.date.today()
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
        lookback = datetime.timedelta(days=BACKFILL_LOOKBACK_DAYS)
        start = last_ts.date() if last_ts else today - lookback
        if start >= today:
            continue
        try:
            bars = await client.bars(vendor_key, Timeframe.ONE_MIN, start, today)
        except Exception:
            logger.exception("restart-recovery bar backfill failed for %s", u.symbol)
            continue
        n = await write_underlying_bars_batch(
            session, u.instrument_id, Timeframe.ONE_MIN.value, provider, bars
        )
        if n:
            logger.info("backfilled %d 1m bars for %s (%s -> today)", n, u.symbol, start)


# --- T1: REST chain poll ----------------------------------------------------


async def poll_chain_once(
    session: AsyncSession,
    client: MarketDataProvider,
    provider: str,
    segment: Segment,
    underlying: Instrument,
) -> None:
    vendor_key = _provider_key(underlying, provider)
    if vendor_key is None:
        return
    expiries = await client.list_expiries(vendor_key)
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
    underlyings_by_segment: dict[Segment, list[Instrument]],
) -> None:
    while True:
        cycle_start = datetime.datetime.now(datetime.UTC)
        for segment, underlyings in underlyings_by_segment.items():
            for u in underlyings:
                try:
                    async with sessionmaker() as session:
                        await poll_chain_once(session, client, provider, segment, u)
                except Exception:
                    logger.exception("T1 poll failed for %s (%s)", u.symbol, segment.value)

        try:
            quota = await client.quota()
            async with sessionmaker() as session:
                await update_feed_health(session, provider, quota_used_pct=quota.used_pct)
        except Exception:
            logger.exception("quota check failed for %s", provider)

        elapsed = (datetime.datetime.now(datetime.UTC) - cycle_start).total_seconds()
        await asyncio.sleep(max(0.0, T1_POLL_INTERVAL.total_seconds() - elapsed))


# --- WS stream ---------------------------------------------------------------


async def _resolve_ws_keys(
    session: AsyncSession, provider: str, underlyings: list[Instrument]
) -> list[str]:
    """Upstox subscribes per option-contract instrument key; LSE subscribes
    per underlying (subscribe_options expands to the whole chain server-side)
    — real vendor asymmetry, not a choice. The Upstox leg universe comes from
    `instruments` (kept current by the T1 poll's upserts), so a brand-new
    strike streams from the next reconnect, not instantly — the T1 poll
    still records it every cycle regardless, so nothing is lost."""
    if provider == "lse":
        return [k for u in underlyings if (k := _provider_key(u, provider)) is not None]

    symbols = [u.symbol for u in underlyings]
    if not symbols:
        return []
    rows = await session.scalars(
        select(Instrument).where(
            Instrument.underlying_symbol.in_(symbols),
            Instrument.kind == InstrumentKind.OPTION,
            Instrument.expiry.is_not(None),
            Instrument.expiry >= datetime.date.today(),
        )
    )
    return [k for r in rows if (k := _provider_key(r, provider)) is not None]


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

    async def flush(session: AsyncSession, connected: bool) -> None:
        if buffer:
            n = await write_option_quotes_batch(session, list(buffer))
            buffer.clear()
            logger.debug("flushed %d ticks for %s", n, provider)
        await update_feed_health(
            session,
            provider,
            connected=connected,
            last_message_at=datetime.datetime.now(datetime.UTC),
            subscribed_count=len(instrument_ids),
        )

    while True:
        try:
            async with sessionmaker() as session:
                vendor_keys = await _resolve_ws_keys(session, provider, underlyings)
            if not vendor_keys:
                logger.warning("%s: no WS keys to subscribe (empty watchlist/legs)", provider)
                await asyncio.sleep(T1_POLL_INTERVAL.total_seconds())
                continue

            stream = client.subscribe(vendor_keys, FeedMode.FULL)
            async with sessionmaker() as session:
                last_flush = datetime.datetime.now(datetime.UTC)
                while True:
                    remaining = (
                        last_flush + WS_FLUSH_INTERVAL - datetime.datetime.now(datetime.UTC)
                    ).total_seconds()
                    try:
                        tick = await asyncio.wait_for(anext(stream), timeout=max(remaining, 0.1))
                    except TimeoutError:
                        await flush(session, connected=True)
                        last_flush = datetime.datetime.now(datetime.UTC)
                        continue
                    except StopAsyncIteration:
                        # A clean end (connection closed, no exception) still
                        # gets a short pause before resubscribing — otherwise
                        # a persistent rejection (e.g. an expired token) would
                        # hot-loop the authorize handshake instead of backing off.
                        await flush(session, connected=False)
                        await asyncio.sleep(2.0)
                        break

                    now = datetime.datetime.now(datetime.UTC)
                    prev = prev_ticks.get(tick.instrument_key)
                    quality = flag_tick(
                        tick, now=now, prev_tick=prev, expected_interval=WS_EXPECTED_TICK_INTERVAL
                    )
                    prev_ticks[tick.instrument_key] = tick

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
                        await flush(session, connected=True)
                        last_flush = datetime.datetime.now(datetime.UTC)
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
            reconnect_wait = min(reconnect_wait * 2, 60.0)


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
            await recover_underlying_bars(session, client, provider, all_underlyings)

        await asyncio.gather(
            t1_poll_loop(sessionmaker, client, provider, underlyings_by_segment),
            ws_stream_loop(sessionmaker, client, provider, all_underlyings),
        )
    finally:
        await client.aclose()
