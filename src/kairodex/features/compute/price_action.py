"""Trend, VWAP, opening range, volume profile, price acceptance —
ARCHITECTURE.md §9's launch-set bullets 3-7."""

from __future__ import annotations

import datetime
import math

from kairodex.data.types import Bar
from kairodex.features.registry import register
from kairodex.features.types import FeatureContext, Fidelity, Tier

_TREND_FAST = 8
_TREND_SLOW = 21
_OPENING_RANGE_MINUTES = 15
_VOLUME_PROFILE_BINS = 20
_ACCEPTANCE_BAND_PCT = 0.005  # 0.5% either side of spot


def _ema(values: list[float], period: int) -> float:
    """Seeded with the first value (a common, simple convention — some
    platforms seed with an SMA of the first `period` values instead; this
    is close enough for a normalized ratio and avoids needing period+1
    values just to produce a first estimate)."""
    alpha = 2.0 / (period + 1)
    value = values[0]
    for v in values[1:]:
        value = alpha * v + (1 - alpha) * value
    return value


@register(
    name="trend_state_strength",
    inputs=["UNDERLYING_BARS"],
    tier=Tier.T1,
    fidelity=Fidelity.EXACT,
    backtestable={"nse": True, "us": True},
    cost_ms=1,
)
def trend_state_strength(
    ctx: FeatureContext, *, fast: int = _TREND_FAST, slow: int = _TREND_SLOW
) -> float | None:
    """(EMA_fast - EMA_slow) / EMA_slow — signed: positive = uptrend,
    negative = downtrend, magnitude = strength. A single float carries
    both "state" and "strength" from the spec's bullet, rather than two
    separate registry entries for what's really one signal."""
    closes = [float(b.close) for b in ctx.underlying_bars]
    if len(closes) < slow:
        return None
    ema_fast = _ema(closes, fast)
    ema_slow = _ema(closes, slow)
    if ema_slow == 0:
        return None
    return (ema_fast - ema_slow) / ema_slow


@register(
    name="vwap_position",
    inputs=["UNDERLYING_BARS"],
    tier=Tier.T1,
    fidelity=Fidelity.EXACT,
    backtestable={"nse": True, "us": True},
    cost_ms=1,
)
def vwap_position(ctx: FeatureContext) -> float | None:
    """Where spot sits relative to session VWAP, in units of the
    volume-weighted band (a z-score, not a raw price distance) — "VWAP
    position & bands" as one number: how many bands away is price."""
    bars = _session_bars(ctx)
    if not bars or ctx.spot is None:
        return None
    typical = [(float(b.high) + float(b.low) + float(b.close)) / 3.0 for b in bars]
    volumes = [float(b.volume) for b in bars]
    total_vol = sum(volumes)
    if total_vol == 0:
        return None
    vwap = sum(tp * v for tp, v in zip(typical, volumes, strict=True)) / total_vol
    variance = sum(v * (tp - vwap) ** 2 for tp, v in zip(typical, volumes, strict=True)) / total_vol
    band = math.sqrt(variance)
    if band == 0:
        return None
    return (ctx.spot - vwap) / band


@register(
    name="opening_range_position",
    inputs=["UNDERLYING_BARS"],
    tier=Tier.T1,
    fidelity=Fidelity.EXACT,
    backtestable={"nse": True, "us": True},
    cost_ms=1,
)
def opening_range_position(
    ctx: FeatureContext, *, minutes: int = _OPENING_RANGE_MINUTES
) -> float | None:
    """(spot - OR_low) / (OR_high - OR_low). 0..1 = inside the opening
    range; <0 or >1 = broken below/above it. Needs `session_open_ts` — the
    loader's job to set, since "when did the session start" isn't
    derivable from bars alone (a gap in the feed shouldn't look like a
    late session open)."""
    if ctx.session_open_ts is None or ctx.spot is None:
        return None
    cutoff = ctx.session_open_ts + datetime.timedelta(minutes=minutes)
    opening_bars = [b for b in ctx.underlying_bars if ctx.session_open_ts <= b.ts < cutoff]
    if not opening_bars:
        return None
    or_high = max(float(b.high) for b in opening_bars)
    or_low = min(float(b.low) for b in opening_bars)
    span = or_high - or_low
    if span == 0:
        return None
    return (ctx.spot - or_low) / span


@register(
    name="volume_profile_poc_distance",
    inputs=["UNDERLYING_BARS"],
    tier=Tier.T1,
    fidelity=Fidelity.PROXY,
    backtestable={"nse": True, "us": True},
    cost_ms=2,
)
def volume_profile_poc_distance(
    ctx: FeatureContext, *, bins: int = _VOLUME_PROFILE_BINS
) -> float | None:
    """Point of control (the price bin with the most volume) via a simple
    fixed-width histogram over the session's bars, each bar's volume
    assigned to its close price's bin. PROXY, not EXACT: a real volume
    profile distributes each bar's volume across its full high-low range;
    assigning it all to the close is a simplification — upgrade if VAH/VAL
    (value area high/low) ever get their own registry entries and need
    the finer distribution.
    Returns (spot - POC) / POC."""
    bars = _session_bars(ctx)
    if not bars or ctx.spot is None:
        return None
    closes = [float(b.close) for b in bars]
    volumes = [float(b.volume) for b in bars]
    lo, hi = min(closes), max(closes)
    if hi == lo:
        return (ctx.spot - lo) / lo if lo != 0 else None
    bin_width = (hi - lo) / bins
    bucket_volume = [0.0] * bins
    for c, v in zip(closes, volumes, strict=True):
        idx = min(int((c - lo) / bin_width), bins - 1)
        bucket_volume[idx] += v
    poc_idx = max(range(bins), key=lambda i: bucket_volume[i])
    poc = lo + (poc_idx + 0.5) * bin_width
    if poc == 0:
        return None
    return (ctx.spot - poc) / poc


@register(
    name="price_acceptance",
    inputs=["UNDERLYING_BARS"],
    tier=Tier.T1,
    fidelity=Fidelity.PROXY,
    backtestable={"nse": True, "us": True},
    cost_ms=1,
)
def price_acceptance(
    ctx: FeatureContext, *, band_pct: float = _ACCEPTANCE_BAND_PCT
) -> float | None:
    """Fraction of recent session volume that traded within `band_pct` of
    current spot — high = price has been "accepted" here (real Market
    Profile TPO/time-at-price is deferred to the backlog per ARCHITECTURE.md
    §9; this is a volume-only proxy for the same idea, hence PROXY not
    EXACT)."""
    bars = _session_bars(ctx)
    if not bars or ctx.spot is None:
        return None
    total_vol = sum(float(b.volume) for b in bars)
    if total_vol == 0:
        return None
    threshold = ctx.spot * band_pct
    accepted = sum(float(b.volume) for b in bars if abs(float(b.close) - ctx.spot) <= threshold)
    return accepted / total_vol


def _session_bars(ctx: FeatureContext) -> list[Bar]:
    if ctx.session_open_ts is None:
        return list(ctx.underlying_bars)
    return [b for b in ctx.underlying_bars if b.ts >= ctx.session_open_ts]
