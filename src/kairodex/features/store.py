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
) -> int:
    """Upsert on the natural key (segment, instrument_id, as_of,
    registry_version) — a re-computation for an instant already stored
    (e.g. a restart replaying the same tick) overwrites rather than
    duplicates, matching `option_quotes`' `ON CONFLICT` pattern.

    Returns the surrogate `feature_vectors.id`, which is what
    `signals.feature_vector_id` points at. `RETURNING` is on the
    conflict path too, so the id comes back whether the row was inserted
    or updated — a restart replaying a tick must link the signal to the
    SAME feature row, not fail to link it at all."""
    stmt = pg_insert(FeatureVector).values(
        segment=segment,
        instrument_id=instrument_id,
        as_of=as_of,
        event_ts=event_ts,
        registry_version=REGISTRY_VERSION,
        values=values,
        quality=quality,
    )
    upsert = stmt.on_conflict_do_update(
        index_elements=["segment", "instrument_id", "as_of", "registry_version"],
        # Subscript access, not `.excluded.values` — caught live: a column
        # literally named "values" collides with `excluded`'s own Python
        # `.values()` method under attribute access, silently returning a
        # bound method instead of the column reference (SQLAlchemy then
        # tried to JSON-serialize *that* as a literal parameter and
        # failed). `excluded["values"]` isn't ambiguous the same way.
        set_={
            "event_ts": stmt.excluded["event_ts"],
            "values": stmt.excluded["values"],
            "quality": stmt.excluded["quality"],
        },
    ).returning(FeatureVector.id)
    feature_vector_id: int | None = await session.scalar(upsert)
    await session.commit()
    if feature_vector_id is None:  # pragma: no cover — ON CONFLICT DO UPDATE always returns
        raise RuntimeError("feature_vector upsert returned no id")
    return feature_vector_id


async def compute_and_store(
    session: AsyncSession,
    ctx: FeatureContext,
    *,
    segment: Segment,
    instrument_id: int,
    event_ts: datetime.datetime | None = None,
) -> tuple[dict[str, float], dict[str, str], int]:
    """Evaluate every registered feature against `ctx` and persist the
    result in one call — the intended entry point for both the live
    recorder and replay, so "computed once per evaluation tick"
    (ARCHITECTURE.md §9) has exactly one code path regardless of caller.

    Wired into `engine.orchestrator.run_entry_tick` on 2026-08-14. Until
    then this function had zero callers and the orchestrator called
    `compute_all` directly, so all 18 features were computed every tick
    and 15 of them discarded: `feature_vectors` held 2 rows (both from
    05 Aug) and `signals.feature_vector_id` was NULL on all 79,209 rows.
    The labels to learn from already existed — 20,729 backfilled
    `forward_outcome`s — and the design matrix to pair them with did not,
    which is what made "learn from past decisions" impossible rather
    than merely unbuilt.

    Returns the id alongside the values so the caller can point
    `signals.feature_vector_id` at the exact row this evaluation used."""
    values, quality = compute_all(ctx)
    feature_vector_id = await write_feature_vector(
        session,
        segment=segment,
        instrument_id=instrument_id,
        as_of=ctx.as_of,
        event_ts=event_ts if event_ts is not None else ctx.as_of,
        values=values,
        quality=quality,
    )
    return values, quality, feature_vector_id


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
