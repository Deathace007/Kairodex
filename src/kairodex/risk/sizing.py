"""Sizing (ARCHITECTURE.md §11):

    risk_budget = equity * base_risk_pct * risk_multiplier(equity, hwm, recent_perf)
    lots        = floor(risk_budget / (stop_distance * lot_size))
    if lots < 1: reject NO_TRADE_MIN_SIZE
    if risk(1 lot) > hard_ceiling_pct * equity: reject SIZE_EXCEEDS_CEILING
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal

from kairodex.config.segments import SegmentRiskConfig

# risk_multiplier curve constants — the doc specifies the *shape* ("scale
# up on profit, reduce on loss... against current equity, never initial
# capital") and explicitly calls it "a config-driven curve," not a formula
# with given numbers. First-pass, documented, not backtested — recalibrate
# once there's real equity-curve data to fit against.
_PROFIT_SCALE_UP = 0.5
_MAX_PROFIT_BONUS = 0.3
_DRAWDOWN_SCALE_DOWN = 1.0
_MAX_DRAWDOWN_PENALTY = 0.5
_LOSS_STREAK_DECAY = 0.85  # each consecutive loss cuts size by 15%
_MULTIPLIER_FLOOR = 0.25
_MULTIPLIER_CEILING = 1.5


def risk_multiplier(equity: Decimal, high_water_mark: Decimal, consecutive_losses: int) -> float:
    """Always evaluated against CURRENT equity vs. HWM, never initial
    capital (§11, explicit). Above HWM: scale up on profit, capped.
    Below HWM: scale down with drawdown, floored. Either way, further
    damped by a consecutive-loss streak."""
    if high_water_mark <= 0:
        return 1.0
    equity_f, hwm_f = float(equity), float(high_water_mark)
    if equity_f >= hwm_f:
        profit_pct = (equity_f - hwm_f) / hwm_f
        base = 1.0 + min(profit_pct * _PROFIT_SCALE_UP, _MAX_PROFIT_BONUS)
    else:
        drawdown_pct = (hwm_f - equity_f) / hwm_f
        base = 1.0 - min(drawdown_pct * _DRAWDOWN_SCALE_DOWN, _MAX_DRAWDOWN_PENALTY)
    streak = _LOSS_STREAK_DECAY ** max(consecutive_losses, 0)
    return max(_MULTIPLIER_FLOOR, min(_MULTIPLIER_CEILING, base * streak))


@dataclass(frozen=True, slots=True)
class SizingResult:
    lots: int
    risk_budget: Decimal
    risk_multiplier_applied: float
    rejected: bool
    reject_reason: str | None = None


def size_position(
    *,
    equity: Decimal,
    high_water_mark: Decimal,
    consecutive_losses: int,
    config: SegmentRiskConfig,
    stop_distance: Decimal,
    lot_size: int,
) -> SizingResult:
    if stop_distance <= 0:
        return SizingResult(0, Decimal(0), 1.0, True, "INVALID_STOP_DISTANCE")
    mult = risk_multiplier(equity, high_water_mark, consecutive_losses)
    risk_budget = equity * Decimal(str(config.base_risk_pct)) * Decimal(str(mult))
    lots = math.floor(risk_budget / (stop_distance * lot_size))
    if lots < 1:
        return SizingResult(0, risk_budget, mult, True, "NO_TRADE_MIN_SIZE")
    one_lot_risk = stop_distance * lot_size
    if one_lot_risk > Decimal(str(config.hard_ceiling_pct)) * equity:
        return SizingResult(0, risk_budget, mult, True, "SIZE_EXCEEDS_CEILING")
    return SizingResult(lots, risk_budget, mult, False, None)
