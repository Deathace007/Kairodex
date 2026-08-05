"""ATR / realized vol / volatility regime — first bullet group of
ARCHITECTURE.md §9's launch set, "in dependency order" (later features in
other files build on these)."""

from __future__ import annotations

import math
import statistics

from kairodex.data.types import Bar
from kairodex.features.registry import register
from kairodex.features.types import FeatureContext, Fidelity, Tier

_ATR_PERIOD = 14
_REGIME_SHORT = 14
_REGIME_LONG = 50
_SECONDS_PER_YEAR = 365.25 * 24 * 3600


def _true_ranges(bars: list[Bar]) -> list[float]:
    trs = []
    for i in range(1, len(bars)):
        high, low = float(bars[i].high), float(bars[i].low)
        prev_close = float(bars[i - 1].close)
        trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    return trs


@register(
    name="atr",
    inputs=["UNDERLYING_BARS"],
    tier=Tier.T1,
    fidelity=Fidelity.EXACT,
    backtestable={"nse": True, "us": True},
    cost_ms=1,
)
def atr(ctx: FeatureContext, *, period: int = _ATR_PERIOD) -> float | None:
    """Wilder's smoothed ATR — the standard convention (not a plain SMA of
    true range), so this matches what any charting platform calls "ATR"."""
    trs = _true_ranges(ctx.underlying_bars)
    if len(trs) < period:
        return None
    value = sum(trs[:period]) / period
    for tr in trs[period:]:
        value = (value * (period - 1) + tr) / period
    return value


@register(
    name="realized_vol",
    inputs=["UNDERLYING_BARS"],
    tier=Tier.T1,
    fidelity=Fidelity.EXACT,
    backtestable={"nse": True, "us": True},
    cost_ms=1,
)
def realized_vol(ctx: FeatureContext) -> float | None:
    """Annualized stdev of log returns. Annualized off the *actual* median
    spacing between bars (calendar time), not an assumed daily/intraday
    cadence — robust to whatever bar timeframe the loader handed in,
    without FeatureContext needing to separately carry "what timeframe are
    these." ponytail: calendar-time annualization (365.25 days/yr) rather
    than trading-time (~252 sessions/yr) — simpler, no market-calendar
    dependency; upgrade to trading-time if this needs to line up with a
    specific vendor's own IV annualization convention."""
    bars = ctx.underlying_bars
    if len(bars) < 3:
        return None
    closes = [float(b.close) for b in bars]
    log_returns = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
    stdev = statistics.stdev(log_returns)  # sample stdev, n-1 denominator

    deltas = [(bars[i].ts - bars[i - 1].ts).total_seconds() for i in range(1, len(bars))]
    median_delta = statistics.median(deltas)
    if median_delta <= 0:
        return None
    periods_per_year = _SECONDS_PER_YEAR / median_delta
    return stdev * math.sqrt(periods_per_year)


@register(
    name="volatility_regime",
    inputs=["UNDERLYING_BARS"],
    tier=Tier.T1,
    fidelity=Fidelity.EXACT,
    backtestable={"nse": True, "us": True},
    cost_ms=1,
)
def volatility_regime(
    ctx: FeatureContext, *, short_period: int = _REGIME_SHORT, long_period: int = _REGIME_LONG
) -> float | None:
    """Short-window average true range over long-window average true range.
    >1 => expanding (recent ranges wider than the baseline), <1 =>
    contracting. A ratio rather than an EXPANSION/CONTRACTION/NEUTRAL label
    so it stores as a plain float in feature_vectors.values like every
    other feature — a consumer can threshold it however it wants."""
    trs = _true_ranges(ctx.underlying_bars)
    if len(trs) < long_period:
        return None
    short_atr = sum(trs[-short_period:]) / short_period
    long_atr = sum(trs[-long_period:]) / long_period
    if long_atr == 0:
        return None
    return short_atr / long_atr
