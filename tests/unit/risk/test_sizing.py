from decimal import Decimal

import pytest

from kairodex.config.segments import SegmentRiskConfig, get_segment_config
from kairodex.core.enums import Segment
from kairodex.risk.sizing import risk_multiplier, size_position


def test_risk_multiplier_at_hwm_no_losses_is_one():
    assert risk_multiplier(Decimal(50000), Decimal(50000), 0) == pytest.approx(1.0)


def test_risk_multiplier_above_hwm_scales_up():
    """20% above HWM: bonus = min(0.20*0.5, 0.3) = 0.10 -> multiplier 1.10."""
    result = risk_multiplier(Decimal(60000), Decimal(50000), 0)
    assert result == pytest.approx(1.10, abs=1e-9)


def test_risk_multiplier_profit_bonus_is_capped():
    """80% above HWM: raw bonus 0.40 > cap 0.3 -> multiplier 1.30, not 1.40."""
    result = risk_multiplier(Decimal(90000), Decimal(50000), 0)
    assert result == pytest.approx(1.30, abs=1e-9)


def test_risk_multiplier_below_hwm_scales_down():
    """20% drawdown: penalty = min(0.20*1.0, 0.5) = 0.20 -> multiplier 0.80."""
    result = risk_multiplier(Decimal(40000), Decimal(50000), 0)
    assert result == pytest.approx(0.80, abs=1e-9)


def test_risk_multiplier_drawdown_penalty_is_floored():
    """70% drawdown: raw penalty 0.70 > cap 0.5 -> base 0.50, still above the
    overall floor 0.25, so multiplier is 0.50."""
    result = risk_multiplier(Decimal(15000), Decimal(50000), 0)
    assert result == pytest.approx(0.50, abs=1e-9)


def test_risk_multiplier_loss_streak_compounds():
    """At HWM, 3 consecutive losses: streak = 0.85^3 = 0.614125."""
    result = risk_multiplier(Decimal(50000), Decimal(50000), 3)
    assert result == pytest.approx(0.85**3, abs=1e-9)


def test_risk_multiplier_overall_floor():
    """99% drawdown (base clamps to 0.50) + 10 consecutive losses
    (streak=0.85^10=0.196874...) -> product 0.0984... clamped to floor 0.25."""
    result = risk_multiplier(Decimal(500), Decimal(50000), 10)
    assert result == pytest.approx(0.25, abs=1e-9)


def test_risk_multiplier_zero_hwm_is_neutral():
    assert risk_multiplier(Decimal(1000), Decimal(0), 0) == 1.0


@pytest.mark.parametrize("segment", list(Segment))
def test_size_position_normal_case_hand_computed(segment):
    """base_risk_pct varies per segment but the arithmetic is the same
    shape: budget = equity * base_risk_pct * 1.0 (at HWM, no losses),
    lots = floor(budget / (stop_distance * lot_size))."""
    config = get_segment_config(segment)
    equity = Decimal(str(config.capital))
    stop_distance = Decimal("2.0")
    lot_size = 25
    result = size_position(
        equity=equity,
        high_water_mark=equity,
        consecutive_losses=0,
        config=config,
        stop_distance=stop_distance,
        lot_size=lot_size,
        # Deliberately tiny — isolates this test to the risk-budget formula
        # alone, so the premium/exposure caps (tested separately below)
        # never become the binding constraint here.
        premium_per_lot=Decimal("0.01"),
    )
    expected_budget = equity * Decimal(str(config.base_risk_pct))
    expected_lots = int(expected_budget // (stop_distance * lot_size))
    if expected_lots < 1:
        assert result.rejected and result.reject_reason == "NO_TRADE_MIN_SIZE"
    else:
        assert result.lots == expected_lots
        assert not result.rejected


def test_size_position_rejects_below_min_size():
    config = SegmentRiskConfig(
        capital=10000, currency="USD", base_risk_pct=0.01, hard_ceiling_pct=0.5,
        max_premium_pct=0.5, max_concurrent=5, daily_loss_limit_pct=0.1,
        weekly_loss_limit_pct=0.2, max_drawdown_pct=0.3, exposure_cap_pct=0.3,
        min_liquidity_score=0.3, reentry_cooldown_minutes=30,
    )
    # budget = 10000*0.01 = 100; one lot = 200*10 = 2000 -> 0 lots
    result = size_position(
        equity=Decimal(10000), high_water_mark=Decimal(10000), consecutive_losses=0,
        config=config, stop_distance=Decimal(200), lot_size=10,
        premium_per_lot=Decimal("0.01"),
    )
    assert result.rejected
    assert result.reject_reason == "NO_TRADE_MIN_SIZE"


def test_size_position_rejects_size_exceeding_ceiling():
    """Deliberately contrived config (hard_ceiling_pct lower than what the
    risk budget alone would allow) to exercise this branch in isolation:
    budget=1000, one_lot_risk=600 -> lots=1 (passes min-size), but
    600 > ceiling(500) -> rejected."""
    config = SegmentRiskConfig(
        capital=10000, currency="USD", base_risk_pct=0.10, hard_ceiling_pct=0.05,
        max_premium_pct=0.5, max_concurrent=5, daily_loss_limit_pct=0.1,
        weekly_loss_limit_pct=0.2, max_drawdown_pct=0.3, exposure_cap_pct=0.3,
        min_liquidity_score=0.3, reentry_cooldown_minutes=30,
    )
    result = size_position(
        equity=Decimal(10000), high_water_mark=Decimal(10000), consecutive_losses=0,
        config=config, stop_distance=Decimal(60), lot_size=10,
        premium_per_lot=Decimal("0.01"),
    )
    assert result.rejected
    assert result.reject_reason == "SIZE_EXCEEDS_CEILING"


def test_size_position_rejects_invalid_stop_distance():
    config = get_segment_config(Segment.NSE_STOCK)
    result = size_position(
        equity=Decimal(50000), high_water_mark=Decimal(50000), consecutive_losses=0,
        config=config, stop_distance=Decimal(0), lot_size=25,
        premium_per_lot=Decimal("1.0"),
    )
    assert result.rejected
    assert result.reject_reason == "INVALID_STOP_DISTANCE"


def test_size_position_rejects_invalid_premium():
    config = get_segment_config(Segment.NSE_STOCK)
    result = size_position(
        equity=Decimal(50000), high_water_mark=Decimal(50000), consecutive_losses=0,
        config=config, stop_distance=Decimal(2), lot_size=25,
        premium_per_lot=Decimal(0),
    )
    assert result.rejected
    assert result.reject_reason == "INVALID_PREMIUM"


def test_size_position_capped_by_max_premium_pct():
    """budget = 10000*0.10 = 1000; risk-budget lots = floor(1000/(10*10)) =
    10. But premium_per_lot=50 -> premium/lot = 50*10 = 500, and
    max_premium_pct=0.05 -> premium cap = 0.05*10000 = 500 ->
    max_premium_lots = floor(500/500) = 1. The real, previously-uncapped
    bug: risk-budget sizing alone would have committed 10x the intended
    premium ceiling."""
    config = SegmentRiskConfig(
        capital=10000, currency="USD", base_risk_pct=0.10, hard_ceiling_pct=0.5,
        max_premium_pct=0.05, max_concurrent=5, daily_loss_limit_pct=0.1,
        weekly_loss_limit_pct=0.2, max_drawdown_pct=0.3, exposure_cap_pct=0.30,
        min_liquidity_score=0.3, reentry_cooldown_minutes=30,
    )
    result = size_position(
        equity=Decimal(10000), high_water_mark=Decimal(10000), consecutive_losses=0,
        config=config, stop_distance=Decimal(10), lot_size=10,
        premium_per_lot=Decimal(50),
    )
    assert not result.rejected
    assert result.lots == 1


def test_size_position_capped_by_exposure():
    """Same risk-budget (10 lots) and premium cap (100 lots, not binding)
    as above, but total_exposure=2900 against exposure_cap_pct=0.30 ->
    exposure_room = 0.30*10000 - 2900 = 100; premium/lot = 5*10 = 50 ->
    max_exposure_lots = floor(100/50) = 2."""
    config = SegmentRiskConfig(
        capital=10000, currency="USD", base_risk_pct=0.10, hard_ceiling_pct=0.5,
        max_premium_pct=0.5, max_concurrent=5, daily_loss_limit_pct=0.1,
        weekly_loss_limit_pct=0.2, max_drawdown_pct=0.3, exposure_cap_pct=0.30,
        min_liquidity_score=0.3, reentry_cooldown_minutes=30,
    )
    result = size_position(
        equity=Decimal(10000), high_water_mark=Decimal(10000), consecutive_losses=0,
        config=config, stop_distance=Decimal(10), lot_size=10,
        premium_per_lot=Decimal(5), total_exposure=Decimal(2900),
    )
    assert not result.rejected
    assert result.lots == 2
