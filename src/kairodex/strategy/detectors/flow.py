"""FLOW family: the standard OI/price-change convention (used routinely
in NSE market commentary) — four combinations, direction from price,
conviction from whether OI agrees with it:

  price up   + OI up   -> long buildup    -> bullish, full conviction
  price up   + OI down -> short covering  -> bullish, half conviction
  price down + OI up   -> short buildup   -> bearish, full conviction
  price down + OI down -> long unwinding  -> bearish, half conviction
"""

from __future__ import annotations

import math

from kairodex.strategy.types import DetectorFamily, Evidence, MarketContext

_NAME = "oi_price_flow"
_PRICE_SCALE = 0.01  # ponytail: first-pass — a 1% window move maps to tanh(1)=0.76;
# recalibrate once backtesting can measure what move size actually matters.
_COVERING_CONVICTION = 0.5


def oi_price_flow_detector(ctx: MarketContext) -> Evidence | None:
    bars = ctx.feature_ctx.underlying_bars
    oi_change = ctx.features.get("oi_change")
    if oi_change is None or len(bars) < 2:
        return None
    first_close, last_close = float(bars[0].close), float(bars[-1].close)
    if first_close == 0:
        return None
    price_change_pct = (last_close - first_close) / first_close
    if price_change_pct == 0:
        return None

    # Conviction comes from OI direction alone (buildup vs. unwind/cover),
    # not from comparing OI's sign to price's — both "buildup" rows in the
    # module docstring's table (long buildup AND short buildup) are OI up,
    # both "weak" rows (short covering AND long unwinding) are OI down.
    is_buildup = oi_change > 0
    conviction = 1.0 if is_buildup else _COVERING_CONVICTION
    score = math.tanh(price_change_pct / _PRICE_SCALE) * conviction

    if price_change_pct > 0:
        label = "long buildup" if is_buildup else "short covering"
    else:
        label = "short buildup" if is_buildup else "long unwinding"
    return Evidence(
        detector=_NAME,
        family=DetectorFamily.FLOW,
        score=score,
        weight=1.0,
        rationale=(
            f"price_change={price_change_pct:.4f}, oi_change={oi_change:.4f} "
            f"({label}) -> score={score:.3f}"
        ),
    )
