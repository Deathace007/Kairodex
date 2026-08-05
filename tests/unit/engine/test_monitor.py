"""Every R-multiple/trailing-stop example is worked by hand in its own
test docstring."""

import dataclasses
import datetime
from decimal import Decimal

import pytest

from kairodex.core.enums import Segment, Side
from kairodex.engine.monitor import (
    Position,
    evaluate_exits,
    event_exit_check,
    profit_target_check,
    r_multiple_partial_exit_check,
    stop_loss_check,
    time_exit_check,
    trailing_stop_check,
)

_NOW = datetime.datetime(2026, 8, 5, 10, 0, tzinfo=datetime.UTC)


def _position(**overrides: object) -> Position:
    base = Position(
        trade_id=1,
        segment=Segment.NSE_INDEX,
        instrument_id=1,
        underlying_symbol="RELIANCE",
        side=Side.BUY,
        qty_lots=10,
        lot_size=25,
        avg_entry=Decimal(100),
        opened_at=_NOW,
        stop_price=Decimal(90),
        initial_stop_price=Decimal(90),  # risk per lot = 10
        current_mark=Decimal(100),
        high_water_mark_price=Decimal(100),
        profit_target=Decimal(130),
        r_multiple_targets=(1.0, 2.0),
    )
    return dataclasses.replace(base, **overrides)  # type: ignore[arg-type]


# --- stop_loss ---------------------------------------------------------


def test_stop_loss_fires_at_exact_stop():
    result = stop_loss_check(_position(current_mark=Decimal(90)))
    assert result is not None
    assert result.reason == "STOP_LOSS"
    assert result.qty_lots == 10


def test_stop_loss_fires_below_stop():
    result = stop_loss_check(_position(current_mark=Decimal(85)))
    assert result is not None


def test_stop_loss_does_not_fire_above_stop():
    assert stop_loss_check(_position(current_mark=Decimal(91))) is None


# --- trailing_stop -------------------------------------------------------


def test_trailing_stop_no_action_when_hwm_close_to_entry():
    """hwm=100, trail_pct=0.30 -> trailed_stop=70, below the fixed
    stop_price(90), so effective_stop stays 90 (max of the two).
    current_mark=95 > 90 -> no exit, and 90 is not > stop_price(90),
    so no ratchet either."""
    position = _position(high_water_mark_price=Decimal(100), current_mark=Decimal(95))
    assert trailing_stop_check(position) is None


def test_trailing_stop_ratchets_up_without_exiting():
    """hwm=150 -> trailed_stop=150*0.7=105 > stop_price(90) ->
    effective_stop=105. current_mark=110 > 105 -> no exit, but the stop
    should ratchet up to 105."""
    result = trailing_stop_check(
        _position(high_water_mark_price=Decimal(150), current_mark=Decimal(110))
    )
    assert result is not None
    assert result.reason == "STOP_RATCHET"
    assert result.qty_lots == 0
    assert result.new_stop_price == Decimal("105.0")


def test_trailing_stop_exits_when_mark_falls_through_trailed_level():
    """Same hwm=150 -> effective_stop=105, but current_mark=100 <= 105 ->
    exit at the trailed level, not the original stop_price."""
    result = trailing_stop_check(
        _position(high_water_mark_price=Decimal(150), current_mark=Decimal(100))
    )
    assert result is not None
    assert result.reason == "TRAILING_STOP"
    assert result.qty_lots == 10
    assert result.new_stop_price == Decimal("105.0")


# --- profit_target ---------------------------------------------------------


def test_profit_target_fires_at_exact_target():
    result = profit_target_check(_position(current_mark=Decimal(130)))
    assert result is not None
    assert result.reason == "PROFIT_TARGET"


def test_profit_target_does_not_fire_below_target():
    assert profit_target_check(_position(current_mark=Decimal(129))) is None


def test_profit_target_none_when_not_configured():
    assert profit_target_check(_position(current_mark=Decimal(999), profit_target=None)) is None


# --- r_multiple / partial exits ---------------------------------------------


def test_r_multiple_hand_computed():
    """risk_per_lot = 100-90 = 10. mark=115 -> r = (115-100)/10 = 1.5."""
    position = _position(current_mark=Decimal(115))
    assert position.r_multiple == pytest.approx(1.5, abs=1e-9)


def test_r_multiple_none_when_no_real_risk_distance():
    position = _position(initial_stop_price=Decimal(100))  # risk_per_lot = 0
    assert position.r_multiple is None


def test_partial_exit_fires_at_lowest_untaken_target_first():
    """mark=120 -> r=(120-100)/10=2.0, qualifying for both targets (1.0
    and 2.0), but 1.0 hasn't been taken yet -> fires R1, not R2."""
    result = r_multiple_partial_exit_check(_position(current_mark=Decimal(120)))
    assert result is not None
    assert result.reason == "PARTIAL_EXIT_R1"
    assert result.qty_lots == 5  # round(10 * 0.5)


def test_partial_exit_skips_already_taken_target():
    """Same r=2.0, but target 1.0 already taken -> fires R2 against the
    CURRENT (already-reduced) qty_lots=4 -> round(4*0.5)=2."""
    taken = frozenset({1.0})
    position = _position(current_mark=Decimal(120), qty_lots=4, partial_exits_taken=taken)
    result = r_multiple_partial_exit_check(position)
    assert result is not None
    assert result.reason == "PARTIAL_EXIT_R2"
    assert result.qty_lots == 2


def test_partial_exit_none_when_below_first_target():
    """mark=105 -> r=0.5, below the first target (1.0)."""
    assert r_multiple_partial_exit_check(_position(current_mark=Decimal(105))) is None


def test_partial_exit_none_when_all_targets_taken():
    position = _position(current_mark=Decimal(200), partial_exits_taken=frozenset({1.0, 2.0}))
    assert r_multiple_partial_exit_check(position) is None


# --- time / event exit ------------------------------------------------------


def test_time_exit_fires_at_exact_limit():
    position = _position(max_holding_secs=3600)
    result = time_exit_check(position, _NOW + datetime.timedelta(seconds=3600))
    assert result is not None
    assert result.reason == "TIME_EXIT"


def test_time_exit_does_not_fire_before_limit():
    position = _position(max_holding_secs=3600)
    assert time_exit_check(position, _NOW + datetime.timedelta(seconds=3599)) is None


def test_time_exit_none_when_not_configured():
    position = _position(max_holding_secs=None)
    assert time_exit_check(position, _NOW + datetime.timedelta(days=999)) is None


def test_event_exit_fires_when_blackout_active():
    result = event_exit_check(_position(), blackout_active=True)
    assert result is not None
    assert result.reason == "EVENT_EXIT"


def test_event_exit_none_when_no_blackout():
    assert event_exit_check(_position(), blackout_active=False) is None


# --- evaluate_exits priority order ------------------------------------------


def test_evaluate_exits_stop_loss_beats_time_exit():
    """Both conditions true simultaneously (mark at stop, AND past max
    holding time) — stop-loss must win, since it's checked first
    (risk protection before mandatory time exit)."""
    position = _position(current_mark=Decimal(85), max_holding_secs=100)
    result = evaluate_exits(position, _NOW + datetime.timedelta(seconds=200))
    assert result is not None
    assert result.reason == "STOP_LOSS"


def test_evaluate_exits_returns_none_when_nothing_triggers():
    result = evaluate_exits(_position(current_mark=Decimal(105)), _NOW)
    assert result is None


def test_evaluate_exits_falls_through_to_profit_target():
    position = _position(current_mark=Decimal(130))
    result = evaluate_exits(position, _NOW)
    assert result is not None
    assert result.reason == "PROFIT_TARGET"
