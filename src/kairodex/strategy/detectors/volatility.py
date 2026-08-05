"""VOLATILITY family: options-market sentiment via put/call IV skew."""

from __future__ import annotations

import math

from kairodex.strategy.types import DetectorFamily, Evidence, MarketContext

_NAME = "iv_skew_sentiment"
_SCALE = 5.0  # percentage-IV-points; ponytail: first-pass — real skew observed
# live (P2's NIFTY verification) was in the low single digits to ~tens of
# points; recalibrate against a real skew distribution once backtesting exists.


def iv_skew_detector(ctx: MarketContext) -> Evidence | None:
    """`iv_skew` (P2) = put IV - call IV on the front expiry — the
    standard "are options traders paying up for downside protection"
    read. Elevated put skew (positive) is a bearish sentiment signal,
    hence the sign flip; a *negative* skew (calls bid richer than puts)
    reads bullish."""
    value = ctx.features.get("iv_skew")
    if value is None:
        return None
    score = math.tanh(-value / _SCALE)
    return Evidence(
        detector=_NAME,
        family=DetectorFamily.VOLATILITY,
        score=score,
        weight=1.0,
        rationale=f"iv_skew={value:.4f} -> score={score:.3f}",
    )
