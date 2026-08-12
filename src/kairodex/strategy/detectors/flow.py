"""FLOW family: the standard OI/price-change convention (used routinely
in NSE market commentary) — four combinations, direction from price,
conviction from whether OI agrees with it:

  price up   + OI up   -> long buildup    -> bullish, full conviction
  price up   + OI down -> short covering  -> bullish, half conviction
  price down + OI up   -> short buildup   -> bearish, full conviction
  price down + OI down -> long unwinding  -> bearish, half conviction
"""

from __future__ import annotations

import datetime
import math

from kairodex.strategy.types import DetectorFamily, Evidence, MarketContext

_NAME = "oi_price_flow"

# The OI window, and therefore the price window too — the convention in the
# module docstring compares OI and price over the SAME interval, and until
# 2026-08-12 this detector did not: it took price change across the whole
# `underlying_bars` window (5 days of 1m bars, per features/loader.py) and
# would have paired a five-day move with a one-tick OI change.
#
# 30 minutes, measured. Across 15,739 real NSE chain snapshots the sign of
# chain-total OI change — the only part of `oi_change` this detector reads —
# is 53.5% positive at a 1-minute horizon, i.e. a coin flip deciding between
# full and half conviction. It separates as the window widens: 56.4% at 5m,
# 58.1% at 15m, 59.3% at 30m. `live_loop` passes the matching `prior_as_of`.
OI_LOOKBACK = datetime.timedelta(minutes=30)

# p90 of |price change| over that same 30-minute window, measured over the
# same snapshots: 0.0038. Same `p90 -> tanh(1)` convention PROGRESS.md §16a
# established and `relative_strength` already follows. The old 0.01 was a
# first-pass guess paired with a 5-day price window, which would have
# saturated `tanh` at +/-1.0 on nearly every evaluation — a family casting a
# full confluence vote on a degenerate number, exactly the `trend_structure`
# pathology §16a found.
_PRICE_SCALE = 0.0038
_COVERING_CONVICTION = 0.5


def oi_price_flow_detector(ctx: MarketContext) -> Evidence | None:
    bars = ctx.feature_ctx.underlying_bars
    oi_change = ctx.features.get("oi_change")
    if oi_change is None or len(bars) < 2:
        return None

    last = bars[-1]
    cutoff = last.ts - OI_LOOKBACK
    prior_bars = [b for b in bars if b.ts <= cutoff]
    if not prior_bars:
        return None  # not enough session history yet — abstain rather than guess
    first_close, last_close = float(prior_bars[-1].close), float(last.close)
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
