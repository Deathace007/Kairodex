"""Relative strength vs. index / index correlation — ARCHITECTURE.md §9
launch-set bullets 8-9. Both need `ctx.index_bars` aligned to
`ctx.underlying_bars` (same count, same cadence) — the loader's job."""

from __future__ import annotations

import math
import statistics

from kairodex.features.registry import register
from kairodex.features.types import FeatureContext, Fidelity, Tier


@register(
    name="relative_strength_vs_index",
    inputs=["UNDERLYING_BARS", "INDEX_BARS"],
    tier=Tier.T1,
    fidelity=Fidelity.EXACT,
    backtestable={"nse": True, "us": True},
    cost_ms=1,
)
def relative_strength_vs_index(ctx: FeatureContext) -> float | None:
    """Underlying's cumulative return over the window minus the index's —
    positive means outperforming the benchmark, not just "going up".

    Requires equal-length bars, same guard `index_correlation` already
    has — without it, `underlying_bars[0]`/`index_bars[0]` (or `[-1]`)
    aren't guaranteed to be the same instant (a data gap on one side, or
    a different holiday calendar between the underlying's exchange and
    its benchmark, could silently compare mismatched date ranges). This
    was reachable but effectively dead before P4's `index_bars` wiring —
    every caller left it `[]`, so the function always returned `None`;
    now that it's live, a real misalignment here would silently corrupt
    a real feature value rather than just never firing."""
    if len(ctx.underlying_bars) != len(ctx.index_bars):
        return None
    if len(ctx.underlying_bars) < 2 or len(ctx.index_bars) < 2:
        return None
    underlying_return = _cumulative_return(
        float(ctx.underlying_bars[0].close), float(ctx.underlying_bars[-1].close)
    )
    index_return = _cumulative_return(
        float(ctx.index_bars[0].close), float(ctx.index_bars[-1].close)
    )
    if underlying_return is None or index_return is None:
        return None
    return underlying_return - index_return


@register(
    name="index_correlation",
    inputs=["UNDERLYING_BARS", "INDEX_BARS"],
    tier=Tier.T1,
    fidelity=Fidelity.EXACT,
    backtestable={"nse": True, "us": True},
    cost_ms=1,
)
def index_correlation(ctx: FeatureContext) -> float | None:
    """Pearson correlation of log returns, underlying vs. index, over the
    aligned window (positionally paired — `ctx.index_bars[i]` is assumed
    to be the same instant as `ctx.underlying_bars[i]`, the loader's
    responsibility, not something this function can verify from bars
    alone)."""
    u_bars, i_bars = ctx.underlying_bars, ctx.index_bars
    if len(u_bars) != len(i_bars) or len(u_bars) < 3:
        return None
    u_returns = _log_returns([float(b.close) for b in u_bars])
    i_returns = _log_returns([float(b.close) for b in i_bars])
    if statistics.pstdev(u_returns) == 0 or statistics.pstdev(i_returns) == 0:
        return None
    return statistics.correlation(u_returns, i_returns)


def _cumulative_return(first: float, last: float) -> float | None:
    if first == 0:
        return None
    return (last - first) / first


def _log_returns(closes: list[float]) -> list[float]:
    return [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
