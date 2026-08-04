"""Minimal upsert path from vendor DTOs into TimescaleDB.

Scope note: this is deliberately small — enough to satisfy the P0 exit
criterion (pull one live chain from each vendor into Timescale) and to give
P1's recorder a real starting point. It is not the recorder: no tiering, no
batched COPY writes, no quality-flag pipeline. Those are P1 scope
(ARCHITECTURE.md §7, §19) and belong with the ingestion loop, not a one-shot
CLI command.
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kairodex.core.enums import InstrumentKind, Segment
from kairodex.data.types import ChainSnapshot, InstrumentRecord
from kairodex.store.models import ChainSnapshot as ChainSnapshotRow
from kairodex.store.models import Instrument, OptionQuote


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
        await session.flush()
        return existing.instrument_id

    row = Instrument(
        segment=rec.segment,
        kind=rec.kind,
        symbol=rec.symbol,
        exchange=rec.exchange,
        currency=rec.currency,
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
        session.add(
            OptionQuote(
                instrument_id=instrument_id,
                ts=q.ts,
                snapshot_id=snap_id,
                bid=q.bid,
                ask=q.ask,
                bid_sz=q.bid_sz,
                ask_sz=q.ask_sz,
                ltp=q.ltp,
                volume=q.volume,
                oi=q.oi,
                oi_change=q.oi_change,
                underlying_px=q.underlying_px,
                vendor_iv=q.vendor_iv,
                delta=q.delta,
                gamma=q.gamma,
                theta=q.theta,
                vega=q.vega,
                rho=q.rho,
                tier=1,
                source=provider,
                quality=0,
            )
        )

    await session.commit()
    return snap_id
