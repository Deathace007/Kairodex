"""Reconstructs `feature_vectors` for signals that were scored before the
engine persisted them — the design matrix half of the training set whose
labels `backfill.py` already produced.

Why this exists. Until 2026-08-14 `engine.orchestrator` called
`features.registry.compute_all` directly and threw the result away: all
18 registered features were computed on every tick, the strategy read 3,
and nothing was written. `feature_vectors` held 2 rows (both 05 Aug) and
`signals.feature_vector_id` was NULL on all 79,209 rows. Wiring
`compute_and_store` in fixes that going forward, but only going forward —
the 20,729 signals that already carry a backfilled `forward_outcome` are
the only labelled history there is, and they would have had no features
to pair with.

The raw inputs are all still on disk (`underlying_bars` 1m from
2026-07-30, `chain_snapshots` and `option_quotes` from 2026-08-04/06), and
`features.loader.build_context` is a genuine point-in-time read — it
filters `ts <= as_of` on bars and takes the latest chain row at or before
`as_of`, never after. So the features an evaluation *would* have seen are
recoverable exactly, which is the same trick `backfill.py` used for the
labels and §16 used to recalibrate the detectors off `signals.evidence`.

**Reproducing what the engine actually saw is the whole correctness
requirement here**, and two inputs are easy to get wrong because they are
supplied by the caller rather than by `build_context` itself:

- `prior_as_of` must be `ts - flow.OI_LOOKBACK`, exactly what
  `engine.live_loop` passes. `None` yields `prior_chain=[]`, which makes
  `oi_change` return None — the precise bug that left the FLOW family
  dead in every one of 20,399 signals until §18a found it.
- `index_bars` must be injected separately. `build_context` deliberately
  leaves it to the caller, and nothing supplying it is what kept
  `relative_strength` permanently dead before §18a.

Get either wrong and the backfilled rows are quietly a different feature
set from the live ones, which is worse than having none: a model trained
across the join would be learning the seam.
"""

from __future__ import annotations

import dataclasses
import datetime
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kairodex.core.enums import Segment
from kairodex.features import loader as feature_loader
from kairodex.features import store as feature_store
from kairodex.store.models import Instrument, Signal
from kairodex.strategy.detectors import flow

logger = logging.getLogger(__name__)


@dataclasses.dataclass(slots=True)
class FeatureBackfillStats:
    """`no_instrument` and `no_context` are the two ways a signal cannot
    be reconstructed: its underlying row is gone, or the point-in-time
    context came back empty (no bars and no chain at that instant, which
    happens for signals predating the chain history)."""

    scanned: int = 0
    written: int = 0
    no_instrument: int = 0
    no_context: int = 0


async def backfill_feature_vectors(
    session: AsyncSession,
    *,
    segment: Segment,
    since: datetime.datetime,
    until: datetime.datetime | None = None,
    overwrite: bool = False,
    batch_log_every: int = 500,
) -> FeatureBackfillStats:
    """Idempotent and re-runnable: skips signals that already point at a
    feature vector unless `overwrite`. Safe to interrupt — every signal
    is committed as it is written (`compute_and_store` commits), so a
    re-run resumes rather than restarting."""
    until = until or datetime.datetime.now(datetime.UTC)

    query = select(Signal).where(
        Signal.segment == segment, Signal.ts >= since, Signal.ts <= until
    )
    if not overwrite:
        query = query.where(Signal.feature_vector_id.is_(None))
    signals = list(await session.scalars(query.order_by(Signal.ts)))
    if not signals:
        return FeatureBackfillStats()

    stats = FeatureBackfillStats(scanned=len(signals))

    # One Instrument fetch per underlying, not per signal — the same
    # amortisation `backfill.py` applies to its bar loads.
    underlying_ids = {s.underlying_id for s in signals}
    instruments = {
        i.instrument_id: i
        for i in await session.scalars(
            select(Instrument).where(Instrument.instrument_id.in_(underlying_ids))
        )
    }

    for n, signal in enumerate(signals, start=1):
        underlying = instruments.get(signal.underlying_id)
        if underlying is None:
            stats.no_instrument += 1
            continue

        feature_ctx = await feature_loader.build_context(
            session,
            segment=segment,
            underlying=underlying,
            as_of=signal.ts,
            # Exactly `engine.live_loop`'s own argument — see module docstring.
            prior_as_of=signal.ts - flow.OI_LOOKBACK,
        )
        if not feature_ctx.underlying_bars and not feature_ctx.chain:
            stats.no_context += 1
            continue

        index_bars = await feature_loader.load_index_bars(session, segment, signal.ts)
        if index_bars:
            feature_ctx = dataclasses.replace(feature_ctx, index_bars=index_bars)

        _values, _quality, feature_vector_id = await feature_store.compute_and_store(
            session,
            feature_ctx,
            segment=segment,
            instrument_id=underlying.instrument_id,
        )
        signal.feature_vector_id = feature_vector_id
        stats.written += 1

        if n % batch_log_every == 0:
            await session.commit()
            logger.info(
                "%s: %d/%d signals reconstructed", segment.value, n, stats.scanned
            )

    await session.commit()
    logger.info(
        "backfilled features %s: scanned=%d written=%d no_instrument=%d no_context=%d",
        segment.value,
        stats.scanned,
        stats.written,
        stats.no_instrument,
        stats.no_context,
    )
    return stats
