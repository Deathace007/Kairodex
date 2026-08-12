"""Fills in `signals.forward_outcome` for LIVE signals — the column
ARCHITECTURE.md §5.4 describes as "filled in later: did the move happen?"
and which nothing has ever written (0 of 63,335 rows as of 2026-08-12).

Why it exists, concretely. On 2026-08-12 the highest-confidence NSE
signal of the day (0.915, all three detector families near saturation)
became the day's worst trade (-Rs 4,343), while a 0.790 signal on the
same underlying an hour earlier made +Rs 4,020. Five trades is an
anecdote, and closed trades accumulate far too slowly to settle the
question — but every *evaluation* the engine has ever made is already on
disk with its evidence, and the underlying's own subsequent 1-minute
bars are on disk too. That is thousands of observations of "what
happened next" available without a single fill, the same trick §16 used
to recalibrate the detectors off `signals.evidence`.

This is deliberately NOT a P&L measurement. It resolves the UNDERLYING's
forward move in ATR units via `resolve.resolve_forward_outcome` — Track
A's own resolver, reused verbatim — so it answers "did direction resolve
correctly", not "would the option have made money". Option economics
need fills, fees and a real book; direction does not, which is the whole
reason this can run today.

Two honest limits, neither worked around:

- **Signals overlap heavily.** The engine re-evaluates every underlying
  roughly every 90 seconds, so consecutive rows on one underlying share
  almost all of their forward window. These are not independent
  observations and must not be treated as a sample size of n.
- **Resolution is intraday-only**, matching the engine's own regime
  (§15g): forward bars are truncated at the signal's own session date,
  so a 15:20 signal is scored on the ten minutes it would actually have
  had, not on tomorrow's gap.
"""

from __future__ import annotations

import bisect
import dataclasses
import datetime
import logging
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kairodex.backtest import resolve
from kairodex.backtest.runner import _DEFAULT_LOOKBACK_BARS, _load_bars
from kairodex.core.enums import Market, Segment
from kairodex.core.sessions import local_date_for
from kairodex.data.types import Bar, Timeframe
from kairodex.features.compute.volatility import atr as atr_feature
from kairodex.features.types import FeatureContext
from kairodex.store.models import Signal

logger = logging.getLogger(__name__)

# 90 one-minute bars, because that is the engine's own holding regime, not a
# round number: `scratch_exit_after_minutes` is 90 and every closed trade that
# ever worked reached +10% within 82 session-minutes (PROGRESS.md §15a). A
# horizon longer than the engine's own patience would score moves it would
# never have stayed in for.
DEFAULT_MAX_HOLDING_BARS = 90


@dataclasses.dataclass(slots=True)
class BackfillStats:
    """`unresolved` is the interesting one: a signal whose forward window
    ran out before the horizon elapsed AND before a stop/target was hit
    is left NULL rather than scored on a short window, so it can resolve
    on a later run. `no_bars`/`no_atr` are signals we cannot score at
    all (no 1m history for that underlying, or a flat pre-entry window)."""

    scanned: int = 0
    written: int = 0
    unresolved: int = 0
    no_bars: int = 0
    no_atr: int = 0


async def backfill_forward_outcomes(
    session: AsyncSession,
    *,
    segment: Segment,
    since: datetime.datetime,
    until: datetime.datetime | None = None,
    timeframe: Timeframe = Timeframe.ONE_MIN,
    lookback_bars: int = _DEFAULT_LOOKBACK_BARS,
    max_holding_bars: int = DEFAULT_MAX_HOLDING_BARS,
    stop_atr_mult: float = resolve.DEFAULT_STOP_ATR_MULT,
    target_atr_mult: float = resolve.DEFAULT_TARGET_ATR_MULT,
    overwrite: bool = False,
) -> BackfillStats:
    """Idempotent and re-runnable: skips rows that already carry an
    outcome unless `overwrite`, and leaves not-yet-resolvable rows NULL
    so a later run picks them up once their bars exist."""
    until = until or datetime.datetime.now(datetime.UTC)
    market = segment.market

    query = select(Signal).where(
        Signal.segment == segment, Signal.ts >= since, Signal.ts <= until
    )
    if not overwrite:
        query = query.where(Signal.forward_outcome.is_(None))
    signals = list(await session.scalars(query.order_by(Signal.ts)))
    if not signals:
        return BackfillStats()

    stats = BackfillStats(scanned=len(signals))
    by_underlying: dict[int, list[Signal]] = {}
    for s in signals:
        by_underlying.setdefault(s.underlying_id, []).append(s)

    for underlying_id, rows in by_underlying.items():
        # One bar query per underlying, not per signal — same reason
        # `runner.py`'s own docstring gives for loading its range up front.
        margin = datetime.timedelta(minutes=max_holding_bars + lookback_bars + 5)
        bars = await _load_bars(
            session, underlying_id, timeframe, rows[0].ts - margin, rows[-1].ts + margin
        )
        if len(bars) <= lookback_bars:
            stats.no_bars += len(rows)
            continue

        bar_times = [b.ts for b in bars]
        for signal in rows:
            outcome = _resolve_one(
                signal,
                bars,
                bar_times,
                market=market,
                segment=segment,
                timeframe=timeframe,
                lookback_bars=lookback_bars,
                max_holding_bars=max_holding_bars,
                stop_atr_mult=stop_atr_mult,
                target_atr_mult=target_atr_mult,
            )
            if isinstance(outcome, str):  # a skip reason, not an outcome
                setattr(stats, outcome, getattr(stats, outcome) + 1)
                continue
            signal.forward_outcome = outcome
            stats.written += 1

    await session.commit()
    logger.info(
        "backfilled %s: scanned=%d written=%d unresolved=%d no_bars=%d no_atr=%d",
        segment.value,
        stats.scanned,
        stats.written,
        stats.unresolved,
        stats.no_bars,
        stats.no_atr,
    )
    return stats


def _resolve_one(
    signal: Signal,
    bars: list[Bar],
    bar_times: list[datetime.datetime],
    *,
    market: Market,
    segment: Segment,
    timeframe: Timeframe,
    lookback_bars: int,
    max_holding_bars: int,
    stop_atr_mult: float,
    target_atr_mult: float,
) -> dict[str, object] | str:
    """Returns the JSONB payload, or the name of the `BackfillStats`
    counter to bump when this signal cannot be scored."""
    # The entry bar is the last one at or before the signal — the engine
    # scored on data up to `signal.ts`, so anything later is lookahead.
    idx = bisect.bisect_right(bar_times, signal.ts) - 1
    if idx < lookback_bars:
        return "no_bars"

    window = bars[idx - lookback_bars : idx + 1]
    ctx = FeatureContext(
        as_of=bars[idx].ts,
        segment=segment,
        underlying_bars=window,
        chain=[],
        prior_chain=[],
    )
    atr_value = atr_feature(ctx)
    if atr_value is None or atr_value <= 0:
        return "no_atr"

    entry_date = local_date_for(market, signal.ts)
    forward: list[Bar] = []
    for bar in bars[idx + 1 :]:
        if local_date_for(market, bar.ts) != entry_date:
            break  # intraday only (§15g) — never resolve across a session boundary
        forward.append(bar)
        if len(forward) >= max_holding_bars:
            break
    if not forward:
        return "unresolved"

    outcome = resolve.resolve_forward_outcome(
        signal.direction,
        bars[idx].close,
        Decimal(str(atr_value)),
        forward,
        stop_atr_mult=stop_atr_mult,
        target_atr_mult=target_atr_mult,
        max_holding_bars=max_holding_bars,
    )
    if outcome is None:
        return "unresolved"
    # A truncated window that hit neither stop nor target hasn't resolved —
    # scoring it as TIME would systematically label late-session signals as
    # flat. Leave it NULL; a later run resolves it if more bars arrive.
    if outcome.exit_reason == "TIME" and len(forward) < max_holding_bars:
        return "unresolved"

    return {
        "exit_reason": outcome.exit_reason,
        "exit_price": str(outcome.exit_price),
        "bars_held": outcome.bars_held,
        "mfe": str(outcome.mfe),
        "mae": str(outcome.mae),
        "mfe_atr": outcome.mfe_atr,
        "mae_atr": outcome.mae_atr,
        "return_atr": outcome.return_atr,
        # The parameters are part of the answer: a `return_atr` means nothing
        # without the horizon and risk unit it was measured over.
        "entry_price": str(bars[idx].close),
        "atr_at_entry": str(atr_value),
        "timeframe": timeframe.value,
        "max_holding_bars": max_holding_bars,
        "stop_atr_mult": stop_atr_mult,
        "target_atr_mult": target_atr_mult,
        "resolver_version": 1,
    }
