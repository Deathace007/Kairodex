"""Read/write `feature_vectors` (ARCHITECTURE.md §5.3) — the point-in-time
store `kairodex.features.registry.compute_all` writes into, mirroring how
`kairodex.data.ingest` is the write path for `option_quotes`."""

from __future__ import annotations

import datetime

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from kairodex.core.enums import Segment
from kairodex.features.registry import REGISTRY_VERSION, compute_all
from kairodex.features.types import FeatureContext
from kairodex.store.models import FeatureVector


async def write_feature_vector(
    session: AsyncSession,
    *,
    segment: Segment,
    instrument_id: int,
    as_of: datetime.datetime,
    event_ts: datetime.datetime,
    values: dict[str, float],
    quality: dict[str, str],
) -> None:
    """Upsert on the natural key (segment, instrument_id, as_of,
    registry_version) — a re-computation for an instant already stored
    (e.g. a restart replaying the same tick) overwrites rather than
    duplicates, matching `option_quotes`' `ON CONFLICT` pattern."""
    stmt = pg_insert(FeatureVector).values(
        segment=segment,
        instrument_id=instrument_id,
        as_of=as_of,
        event_ts=event_ts,
        registry_version=REGISTRY_VERSION,
        values=values,
        quality=quality,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["segment", "instrument_id", "as_of", "registry_version"],
        set_={
            "event_ts": stmt.excluded.event_ts,
            "values": stmt.excluded.values,
            "quality": stmt.excluded.quality,
        },
    )
    await session.execute(stmt)
    await session.commit()


async def compute_and_store(
    session: AsyncSession,
    ctx: FeatureContext,
    *,
    segment: Segment,
    instrument_id: int,
    event_ts: datetime.datetime | None = None,
) -> tuple[dict[str, float], dict[str, str]]:
    """Evaluate every registered feature against `ctx` and persist the
    result in one call — the intended entry point for both the live
    recorder (once wired in) and replay, so "computed once per evaluation
    tick" (ARCHITECTURE.md §9) has exactly one code path regardless of
    caller."""
    values, quality = compute_all(ctx)
    await write_feature_vector(
        session,
        segment=segment,
        instrument_id=instrument_id,
        as_of=ctx.as_of,
        event_ts=event_ts if event_ts is not None else ctx.as_of,
        values=values,
        quality=quality,
    )
    return values, quality


async def read_feature_vector(
    session: AsyncSession, *, segment: Segment, instrument_id: int, as_of: datetime.datetime
) -> FeatureVector | None:
    return await session.get(
        FeatureVector,
        {
            "segment": segment,
            "instrument_id": instrument_id,
            "as_of": as_of,
            "registry_version": REGISTRY_VERSION,
        },
    )
