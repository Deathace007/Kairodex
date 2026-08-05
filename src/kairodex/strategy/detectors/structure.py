"""STRUCTURE family: trend direction and strength."""

from __future__ import annotations

import math

from kairodex.strategy.types import DetectorFamily, Evidence, MarketContext

_NAME = "trend_structure"
_SCALE = 0.05  # ponytail: first-pass — real values observed live were ~0.001-0.15;
# recalibrate against a real trend_state_strength distribution once backtesting exists.


def trend_structure_detector(ctx: MarketContext) -> Evidence | None:
    """`trend_state_strength` (P2) is already signed — positive EMA
    fast-over-slow spread means uptrend, negative means downtrend. `tanh`
    bounds it into [-1, 1] smoothly rather than a hard clip, so a huge
    trend reading saturates near +-1 instead of just getting clamped
    there identically to a merely-large one."""
    value = ctx.features.get("trend_state_strength")
    if value is None:
        return None
    score = math.tanh(value / _SCALE)
    return Evidence(
        detector=_NAME,
        family=DetectorFamily.STRUCTURE,
        score=score,
        weight=1.0,
        rationale=f"trend_state_strength={value:.4f} -> score={score:.3f}",
    )
