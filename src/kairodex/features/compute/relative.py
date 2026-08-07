"""Relative strength vs. index / index correlation — ARCHITECTURE.md §9
launch-set bullets 8-9. Both pair `ctx.index_bars` with
`ctx.underlying_bars` *by timestamp* (`_aligned_closes`), so neither
depends on the two series happening to have the same length."""

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

    Compares the two series over the instants they actually share (see
    `_aligned_closes`), which is what the old equal-length guard was
    reaching for and did not achieve."""
    pairs = _aligned_closes(ctx)
    if len(pairs) < 2:
        return None
    underlying_return = _cumulative_return(pairs[0][0], pairs[-1][0])
    index_return = _cumulative_return(pairs[0][1], pairs[-1][1])
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
    instants the two series share (`_aligned_closes`)."""
    pairs = _aligned_closes(ctx)
    if len(pairs) < 3:
        return None
    u_returns = _log_returns([u for u, _ in pairs])
    i_returns = _log_returns([i for _, i in pairs])
    if statistics.pstdev(u_returns) == 0 or statistics.pstdev(i_returns) == 0:
        return None
    return statistics.correlation(u_returns, i_returns)


def _aligned_closes(ctx: FeatureContext) -> list[tuple[float, float]]:
    """`(underlying_close, index_close)` for every instant present in both
    series, in time order.

    Pairing by timestamp rather than by position, because equal length is
    not equal cadence and demanding it was a silent kill switch on every
    sparse feed. Measured live 2026-08-07: NSE's Upstox bars are a dense
    uniform minute grid (every watchlist symbol and Nifty 50 alike had
    exactly 1500 bars over the same window, so the old `len(u) != len(i)`
    guard passed), but LSE emits no bar for a minute that did not trade —
    over the same five days SPY had 3798, NVDA 3840, BAC 2343. The only US
    underlying that ever cleared the guard was SPY, comparing itself to
    itself. `relative_strength_vs_index` therefore returned None for
    every US underlying on every call, which left the RELATIVE_STRENGTH
    family permanently dead on US, which left `ConfluenceScorer` with
    only two live families against a `min_families` of 2 — so US needed
    unanimity where NSE needed a majority, and averaged its confidence
    over one fewer (and much stronger) score. That, not any strategy
    judgement, is why us_index's confidence p95 was 0.0239 against
    nse_stock's 0.5509.

    Intersecting timestamps is also strictly safer than the guard it
    replaces: a data gap on one side or a differing holiday calendar used
    to shift every pairing silently, and now simply drops the unmatched
    instants."""
    index_close_at = {b.ts: float(b.close) for b in ctx.index_bars}
    return [
        (float(b.close), index_close_at[b.ts])
        for b in ctx.underlying_bars
        if b.ts in index_close_at
    ]


def _cumulative_return(first: float, last: float) -> float | None:
    if first == 0:
        return None
    return (last - first) / first


def _log_returns(closes: list[float]) -> list[float]:
    return [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
