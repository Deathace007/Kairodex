"""STRUCTURE family: trend direction and strength."""

from __future__ import annotations

import math

from kairodex.strategy.types import DetectorFamily, Evidence, MarketContext

_NAME = "trend_structure"
_SCALE = 0.0018  # calibrated 2026-08-11 against 20,399 real NSE signal evidences
# (PROGRESS.md §16). The first-pass 0.05 assumed values "~0.001-0.15"; the real live
# distribution is |trend_state_strength| p50=0.0006, p90=0.0018 on BOTH NSE segments —
# ~27x smaller than assumed, so every score landed at |0.016| against a [-1,1] range.
# The STRUCTURE family voted on noise while contributing ~nothing to confidence.
# p90 -> tanh(1)=0.76 is the convention used by relative_strength (_SCALE=0.03 vs. its
# own p90=0.022), the one detector whose scale was already right.


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
