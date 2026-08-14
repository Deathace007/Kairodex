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
    premium_per_lot: Decimal,
    total_exposure: Decimal = Decimal(0),
) -> SizingResult:
    if stop_distance <= 0:
        return SizingResult(0, Decimal(0), 1.0, True, "INVALID_STOP_DISTANCE")
    if premium_per_lot <= 0:
        return SizingResult(0, Decimal(0), 1.0, True, "INVALID_PREMIUM")
    mult = risk_multiplier(equity, high_water_mark, consecutive_losses)
    risk_budget = equity * Decimal(str(config.base_risk_pct)) * Decimal(str(mult))
    lots = math.floor(risk_budget / (stop_distance * lot_size))

    # Risk-budget sizing alone doesn't bound total premium committed: since
    # stop_distance is normally some fraction of premium (see
    # orchestrator._DEFAULT_STOP_LOSS_PCT), risk_budget/(stop_distance*
    # lot_size) can size up a lot count whose total premium is a multiple
    # of the risk budget — routinely exceeding max_premium_pct once
    # risk_multiplier scales above 1.0. kairodex.risk.gates only ever
    # checked ONE lot's premium against these same caps, before this
    # function decided the real lot count — cap the lot count here too,
    # against what this trade would actually commit.
    premium_cost_per_lot = premium_per_lot * lot_size
    max_premium_lots = math.floor(
        Decimal(str(config.max_premium_pct)) * equity / premium_cost_per_lot
    )
    exposure_room = Decimal(str(config.exposure_cap_pct)) * equity - total_exposure
    max_exposure_lots = (
        math.floor(exposure_room / premium_cost_per_lot) if exposure_room > 0 else 0
    )

    # Per-slot premium budget: the exposure cap divided by the number of
    # positions the segment is configured to hold at once. Added 2026-08-14
    # (PROGRESS.md §20a) because the three existing caps contradicted each
    # other and the contradiction, not the strategy, was the day's largest
    # loss driver.
    #
    # The arithmetic. `risk_budget / stop_distance` is a NOTIONAL, and since
    # `stop_distance` is `_DEFAULT_STOP_LOSS_PCT` (0.20) of premium, that
    # notional is `equity * base_risk_pct / 0.20` = 0.40 * equity. The
    # premium cap is 0.35 * equity, so it binds on EVERY trade; the exposure
    # cap is 0.40 * equity, so it funds 1.14 such positions. Result:
    # `max_concurrent: 5` was arithmetically unreachable, the first signal
    # of the day was funded in full, and positions 2..N divided a remainder.
    #
    # Live 2026-08-14 this produced notionals from Rs 148 to Rs 17,052 — a
    # 115x spread across trades the risk model believes are one identical
    # unit — with `corr(notional, return) = -1.2%`, i.e. size carried NO
    # information, only variance. Flat-weighting the same 34 trades gives
    # -Rs 3,256 against the -Rs 5,438 booked. The clearest single case: the
    # best trade since the 08-11 reset (+49.5%, 2R in ten minutes) got ONE
    # lot, Rs 391, because two earlier positions had eaten the budget.
    #
    # Expressed as a cap rather than by redefining `risk_budget`, so
    # ARCHITECTURE.md §11's stated formula still holds as written and this
    # sits alongside the other two caps in the same `min()`.
    #
    # ponytail: equal slices, not conviction-weighted. Weighting by a
    # calibrated P(win) is the right eventual answer and is exactly what a
    # meta-label model would supply — but `confidence` is measured
    # ANTI-predictive (§19b), so weighting by it today would be worse than
    # equal. Revisit when something with demonstrated edge can set the weight.
    per_slot_premium = (
        Decimal(str(config.exposure_cap_pct)) * equity / Decimal(max(config.max_concurrent, 1))
    )
    max_slot_lots = math.floor(per_slot_premium / premium_cost_per_lot)

    lots = min(lots, max_premium_lots, max_exposure_lots, max_slot_lots)

    if lots < 1:
        return SizingResult(0, risk_budget, mult, True, "NO_TRADE_MIN_SIZE")
    one_lot_risk = stop_distance * lot_size
    if one_lot_risk > Decimal(str(config.hard_ceiling_pct)) * equity:
        return SizingResult(0, risk_budget, mult, True, "SIZE_EXCEEDS_CEILING")
    return SizingResult(lots, risk_budget, mult, False, None)
