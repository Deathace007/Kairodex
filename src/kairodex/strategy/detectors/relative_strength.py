"""RELATIVE_STRENGTH family: performance vs. the segment's benchmark index."""

from __future__ import annotations

import math

from kairodex.strategy.types import DetectorFamily, Evidence, MarketContext

_NAME = "relative_strength"
_SCALE = 0.03  # ponytail: first-pass — a 3% outperformance window maps to
# tanh(1)=0.76; recalibrate once backtesting can measure what magnitude
# of relative-strength divergence actually predicts continuation.


def relative_strength_detector(ctx: MarketContext) -> Evidence | None:
    """`relative_strength_vs_index` (P2) is the underlying's cumulative
    return minus the index's over the window — positive means
    outperforming the benchmark, independent of whether the underlying
    itself is up or down."""
    value = ctx.features.get("relative_strength_vs_index")
    if value is None:
        return None
    score = math.tanh(value / _SCALE)
    return Evidence(
        detector=_NAME,
        family=DetectorFamily.RELATIVE_STRENGTH,
        score=score,
        weight=1.0,
        rationale=f"relative_strength_vs_index={value:.4f} -> score={score:.3f}",
    )
