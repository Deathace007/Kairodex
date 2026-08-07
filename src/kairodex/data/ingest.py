"""Upsert paths from vendor DTOs into TimescaleDB — instrument master,
one-shot chain snapshots (`store_chain_snapshot`, used by both the P0
`pull-chain` CLI and P1's T1 REST poll), and the batched writers P1's
streamed WS ticks and restart-recovery bar backfill use.
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from kairodex.core.enums import InstrumentKind, Segment
from kairodex.data.quality import flag_tick
from kairodex.data.types import Bar, ChainSnapshot, InstrumentRecord, Tick
from kairodex.store.models import ChainSnapshot as ChainSnapshotRow
from kairodex.store.models import Instrument, OptionQuote, UnderlyingBar


async def upsert_instrument(session: AsyncSession, rec: InstrumentRecord, provider: str) -> int:
    """Upsert by the natural key (exchange, symbol, expiry, strike,
    option_type) and return the instrument_id. Merges provider_ids so the
    same contract seen from two vendors resolves to one row."""
    existing = await session.scalar(
        select(Instrument).where(
            Instrument.exchange == rec.exchange,
            Instrument.symbol == rec.symbol,
            Instrument.expiry == rec.expiry,
            Instrument.strike == rec.strike,
            Instrument.option_type == rec.option_type,
        )
    )
    now = datetime.datetime.now(datetime.UTC)
    if existing is not None:
        existing.provider_ids = {**existing.provider_ids, **rec.provider_ids}
        existing.last_seen = now
        if rec.segment is not None:
            existing.segment = rec.segment
        if rec.underlying_symbol is not None:
            existing.underlying_symbol = rec.underlying_symbol
        await session.flush()
        return existing.instrument_id

    row = Instrument(
        segment=rec.segment,
        kind=rec.kind,
        symbol=rec.symbol,
        exchange=rec.exchange,
        currency=rec.currency,
        underlying_symbol=rec.underlying_symbol,
        strike=rec.strike,
        option_type=rec.option_type,
        expiry=rec.expiry,
        exercise_style=rec.exercise_style.value if rec.exercise_style else None,
        settlement=rec.settlement.value if rec.settlement else None,
        provider_ids=rec.provider_ids,
        first_seen=now,
        last_seen=now,
    )
    session.add(row)
    await session.flush()
    return row.instrument_id


async def store_chain_snapshot(
    session: AsyncSession,
    provider: str,
    snapshot: ChainSnapshot,
    underlying_rec: InstrumentRecord,
    segment: Segment | None,
) -> uuid.UUID:
    """Resolve the underlying + every quoted leg to instrument_ids, then
    write the chain_snapshots + option_quotes rows for one atomic read."""
    underlying_id = await upsert_instrument(session, underlying_rec, provider)

    snap_id = uuid.uuid4()
    session.add(
        ChainSnapshotRow(
            snapshot_id=snap_id,
            underlying_id=underlying_id,
            ts=snapshot.ts,
            contract_count=snapshot.contract_count,
            expected_count=snapshot.expected_count,
            complete=snapshot.complete,
        )
    )

    for q in snapshot.quotes:
        leg_symbol = f"{underlying_rec.symbol} {q.strike} {q.option_type} {snapshot.expiry}"
        leg_rec = InstrumentRecord(
            exchange=underlying_rec.exchange,
            symbol=leg_symbol,
            kind=InstrumentKind.OPTION,
            currency=underlying_rec.currency,
            provider_ids={provider: q.instrument_key},
            segment=segment,
            underlying_symbol=underlying_rec.symbol,
            strike=q.strike,
            option_type=q.option_type,
            expiry=snapshot.expiry,
        )
        instrument_id = await upsert_instrument(session, leg_rec, provider)
        quality = flag_tick(q, now=snapshot.ts)
        session.add(
            OptionQuote(
                snapshot_id=snap_id,
                **option_quote_row(q, instrument_id, provider=provider, tier=1, quality=quality),
            )
        )

    await session.commit()
    return snap_id


def option_quote_row(
    tick: Tick, instrument_id: int, *, provider: str, tier: int, quality: int
) -> dict[str, object]:
    """The Tick -> option_quotes column mapping, shared by the one-shot chain
    upsert above and the streamed batch writer below so the two paths can
    never drift on field names."""
    return dict(
        instrument_id=instrument_id,
        ts=tick.ts,
        bid=tick.bid,
        ask=tick.ask,
        bid_sz=tick.bid_sz,
        ask_sz=tick.ask_sz,
        ltp=tick.ltp,
        volume=tick.volume,
        oi=tick.oi,
        oi_change=tick.oi_change,
        underlying_px=tick.underlying_px,
        vendor_iv=tick.vendor_iv,
        delta=tick.delta,
        gamma=tick.gamma,
        theta=tick.theta,
        vega=tick.vega,
        rho=tick.rho,
        tier=tier,
        source=provider,
        quality=quality,
    )


async def write_option_quotes_batch(session: AsyncSession, rows: list[dict[str, object]]) -> int:
    """Batched upsert for streamed ticks (ARCHITECTURE.md §7): dedupe on the
    natural key `(instrument_id, ts)` via `ON CONFLICT DO NOTHING` — a
    reconnect that re-delivers a tick already written is a no-op, not a
    crash.

    A multi-row `INSERT ... ON CONFLICT` rather than Timescale `COPY`
    # ponytail: at T1's ~2M rows/day/market this is well inside asyncpg's
    # batch-insert throughput; swap to asyncpg COPY if write latency ever
    # becomes the bottleneck (ARCHITECTURE.md §7 names COPY as the design).
    """
    if not rows:
        return 0
    stmt = (
        pg_insert(OptionQuote)
        .values(rows)
        .on_conflict_do_nothing(index_elements=[OptionQuote.instrument_id, OptionQuote.ts])
        .returning(OptionQuote.ts)
    )
    inserted = len((await session.execute(stmt)).all())
    await session.commit()
    return inserted


async def write_underlying_bars_batch(
    session: AsyncSession, instrument_id: int, timeframe: str, provider: str, bars: list[Bar]
) -> int:
    """Batched upsert for `bars()` results — used by restart recovery
    (ARCHITECTURE.md §7) to backfill underlying OHLCV missed while the
    recorder was down. Same dedupe-on-conflict shape as option quotes.

    Returns rows actually inserted (`RETURNING` skips the ones the
    conflict clause dropped), not rows attempted. The difference is not
    cosmetic: the caller logs this number as "backfilled N 1m bars", and
    while it counted attempts it printed a confident `backfilled 942 1m
    bars for AAPL` for a batch in which every single row was a duplicate
    and nothing moved. That log line was the only evidence anyone had
    that the refresh worked, and it said yes while the newest bar in the
    table stayed 14 hours old."""
    if not bars:
        return 0
    rows = [
        dict(
            instrument_id=instrument_id,
            timeframe=timeframe,
            ts=b.ts,
            open=b.open,
            high=b.high,
            low=b.low,
            close=b.close,
            volume=b.volume,
            source=provider,
            quality=0,
        )
        for b in bars
    ]
    stmt = (
        pg_insert(UnderlyingBar)
        .values(rows)
        .on_conflict_do_nothing(
            index_elements=[UnderlyingBar.instrument_id, UnderlyingBar.timeframe, UnderlyingBar.ts]
        )
        .returning(UnderlyingBar.ts)
    )
    inserted = len((await session.execute(stmt)).all())
    await session.commit()
    return inserted
