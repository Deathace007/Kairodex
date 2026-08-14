"""Every R-multiple/trailing-stop example is worked by hand in its own
test docstring."""

import dataclasses
import datetime
from decimal import Decimal

import pytest

from kairodex.core.enums import Market, Segment, Side
from kairodex.core.sessions import session_length_secs, session_seconds_between
from kairodex.engine.monitor import (
    Position,
    evaluate_exits,
    event_exit_check,
    expiry_exit_check,
    profit_target_check,
    r_multiple_partial_exit_check,
    scratch_exit_check,
    session_close_exit_check,
    stop_loss_check,
    time_exit_check,
    trailing_stop_check,
)

# 11:30 IST on a Wednesday — mid-session for NSE. It used to be 10:00 UTC,
# which is 15:30 IST: the closing bell exactly. Harmless until
# `session_close_exit_check` existed, at which point every position built
# from this fixture was sitting inside the intraday rule's cushion.
_NOW = datetime.datetime(2026, 8, 5, 6, 0, tzinfo=datetime.UTC)


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
        # Time rules measure open-market seconds, supplied by the caller.
        # _NOW is 10:00 UTC on a Wednesday = mid-session for both markets,
        # so wall-clock and session time coincide over these short spans.
        held_session_secs=0.0,
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
        _position(high_water_mark_price=Decimal(150), current_mark=Decimal(110)),
        trail_pct=0.30,
    )
    assert result is not None
    assert result.reason == "STOP_RATCHET"
    assert result.qty_lots == 0
    assert result.new_stop_price == Decimal("105.0")


def test_trailing_stop_exits_when_mark_falls_through_trailed_level():
    """Same hwm=150 -> effective_stop=105, but current_mark=100 <= 105 ->
    exit at the trailed level, not the original stop_price."""
    result = trailing_stop_check(
        _position(high_water_mark_price=Decimal(150), current_mark=Decimal(100)),
        trail_pct=0.30,
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
    position = _position(max_holding_secs=3600, held_session_secs=3600)
    result = time_exit_check(position, _NOW + datetime.timedelta(seconds=3600))
    assert result is not None
    assert result.reason == "TIME_EXIT"


def test_time_exit_does_not_fire_before_limit():
    position = _position(max_holding_secs=3600, held_session_secs=3599)
    assert time_exit_check(position, _NOW + datetime.timedelta(seconds=3599)) is None


def test_time_exit_counts_session_time_not_wall_clock():
    """The regression that produced all four of 2026-08-10's TIME_EXITs at
    Monday's opening bell: positions opened the previous week were four
    calendar days old against a 3-day guard, but had seen only two
    sessions. Held over a weekend, wall-clock is far past the limit while
    session time is not — and only session time may fire the exit."""
    opened = datetime.datetime(2026, 8, 6, 10, 0, tzinfo=datetime.UTC)  # Thursday
    monday = datetime.datetime(2026, 8, 10, 4, 0, tzinfo=datetime.UTC)  # 09:30 IST Monday
    assert (monday - opened).total_seconds() > 3 * 24 * 3600  # 4 calendar days
    position = _position(
        opened_at=opened,
        max_holding_secs=int(3 * session_length_secs(Market.NSE)),
        held_session_secs=session_seconds_between(Market.NSE, opened, monday),
    )
    assert time_exit_check(position, monday) is None


def test_time_exit_abstains_when_session_time_not_supplied():
    """Fails closed toward *not* exiting: without a session-time
    measurement the rule has nothing to test, and silently falling back to
    wall-clock is the exact bug this replaced."""
    assert time_exit_check(_position(max_holding_secs=1, held_session_secs=None), _NOW) is None


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


def test_stop_ratchet_does_not_pre_empt_a_partial_exit_on_the_same_tick():
    """Regression, live 2026-08-07. A tick that makes a new high fires the
    trailing-stop ratchet AND may cross an R-target — and the ratchet used
    to match first and return, so the partial never ran.

    Reproduces real trade 4 (Nifty 24750 C) to its own recorded numbers:
    entry 89.37, initial stop 62.5725 -> risk/lot 26.7975, so 1R = 116.1675.
    Its peak mark was 117.35, which is both a new high (hwm was 116.15) and
    past 1R. That single tick was consumed by the ratchet; the next tick was
    113.45 and the position later stopped out at -₹271 after being +31%."""
    position = _position(
        avg_entry=Decimal("89.37"),
        stop_price=Decimal("81.305"),
        initial_stop_price=Decimal("62.5725"),
        current_mark=Decimal("117.35"),
        high_water_mark_price=Decimal("117.35"),
        profit_target=Decimal("178.74"),
        r_multiple_targets=(1.0, 2.0),
        partial_exits_taken=frozenset(),
        qty_lots=1,
    )
    # The ratchet genuinely does want to fire on this tick...
    ratchet = trailing_stop_check(position, trail_pct=0.30)
    assert ratchet is not None and ratchet.qty_lots == 0

    # ...but the partial exit is what must actually happen.
    decision = evaluate_exits(position, _NOW)
    assert decision is not None
    assert decision.reason == "PARTIAL_EXIT_R1"
    assert decision.qty_lots > 0


def test_stop_ratchet_still_returned_when_nothing_else_fires():
    """The ratchet must not be lost — only deprioritised. A new high that
    crosses no target still has to move the stop up."""
    # hwm 200 -> trailed 140, which is above the standing 90 stop, so a
    # ratchet is genuinely due. Nothing else may fire: mark 200 is under the
    # 300 target, and 10R is under the single 50R target.
    position = _position(
        current_mark=Decimal(200), high_water_mark_price=Decimal(200),
        profit_target=Decimal(300), r_multiple_targets=(50.0,),
    )
    decision = evaluate_exits(position, _NOW, trail_pct=0.30)
    assert decision is not None
    assert decision.qty_lots == 0  # ratchet, no exit
    assert decision.new_stop_price == Decimal(200) * Decimal("0.7")


def test_stop_loss_still_wins_over_everything_including_the_ratchet():
    """Priority order is unchanged for real exits: risk protection first."""
    position = _position(current_mark=Decimal(50), high_water_mark_price=Decimal(200))
    decision = evaluate_exits(position, _NOW)
    assert decision is not None
    assert decision.reason == "STOP_LOSS"
    assert decision.qty_lots == position.qty_lots


# --- scratch exit -----------------------------------------------------------


def test_scratch_exit_fires_when_position_never_worked():
    """The measured case, from trade 7 (ADANIENT 3100 C): 90 session-minutes
    in, best-ever excursion +2.3% against an 8% bar. Left alone it ran to a
    -25.9% time exit for -Rs 1,490."""
    position = _position(
        avg_entry=Decimal(100),
        high_water_mark_price=Decimal("102.3"),
        current_mark=Decimal("102.3"),
        held_session_secs=5400,
        scratch_after_secs=5400,
    )
    result = scratch_exit_check(position)
    assert result is not None
    assert result.reason == "SCRATCH_EXIT"
    assert result.qty_lots == 10  # the whole position, not a fraction


def test_scratch_exit_spares_a_position_that_did_work():
    """Trade 3 (BANKNIFTY) was +10.5% at the same 90-minute mark. Every
    closed trade that ever reached +10% was above +8.8% here, so the bar
    separates the two groups cleanly on the real data."""
    position = _position(
        avg_entry=Decimal(100),
        high_water_mark_price=Decimal("110.5"),
        held_session_secs=5400,
        scratch_after_secs=5400,
    )
    assert scratch_exit_check(position) is None


def test_scratch_exit_uses_high_water_mark_not_current_mark():
    """A position that ran to +20% and gave it back has worked; it belongs
    to the stop and the trailing stop, not to this rule."""
    position = _position(
        avg_entry=Decimal(100),
        high_water_mark_price=Decimal(120),
        current_mark=Decimal(99),
        held_session_secs=5400,
        scratch_after_secs=5400,
    )
    assert scratch_exit_check(position) is None


def test_scratch_exit_waits_for_the_full_window():
    position = _position(
        avg_entry=Decimal(100),
        high_water_mark_price=Decimal(100),
        held_session_secs=5399,
        scratch_after_secs=5400,
    )
    assert scratch_exit_check(position) is None


def test_scratch_exit_never_pre_empts_a_real_exit():
    """Priority: a position simultaneously through its stop and past the
    scratch window exits as a STOP_LOSS — the reason recorded has to be the
    one that actually fired, since exit_reason drives every breakdown."""
    position = _position(
        avg_entry=Decimal(100),
        current_mark=Decimal(90),
        high_water_mark_price=Decimal(100),
        held_session_secs=5400,
        scratch_after_secs=5400,
    )
    result = evaluate_exits(position, _NOW)
    assert result is not None
    assert result.reason == "STOP_LOSS"


# --- expiry exit ------------------------------------------------------------


def test_expiry_exit_fires_inside_the_cushion():
    """NSE close is 15:30 IST = 10:00 UTC; the default cushion is 30 min,
    so 09:35 UTC on expiry day fires."""
    expiry = datetime.date(2026, 8, 11)
    position = _position(expiry=expiry)
    now = datetime.datetime(2026, 8, 11, 9, 35, tzinfo=datetime.UTC)
    result = expiry_exit_check(position, now)
    assert result is not None
    assert result.reason == "EXPIRY_EXIT"
    assert result.qty_lots == 10


def test_expiry_exit_does_not_fire_earlier_in_the_expiry_session():
    position = _position(expiry=datetime.date(2026, 8, 11))
    now = datetime.datetime(2026, 8, 11, 9, 25, tzinfo=datetime.UTC)
    assert expiry_exit_check(position, now) is None


def test_expiry_exit_does_not_fire_on_a_later_dated_contract():
    position = _position(expiry=datetime.date(2026, 8, 25))
    now = datetime.datetime(2026, 8, 11, 9, 55, tzinfo=datetime.UTC)
    assert expiry_exit_check(position, now) is None


def test_expiry_exit_abstains_without_an_expiry():
    assert expiry_exit_check(_position(expiry=None), _NOW) is None


# --- session close / intraday rule ------------------------------------------

_NSE_MID = datetime.datetime(2026, 8, 5, 6, 0, tzinfo=datetime.UTC)  # 11:30 IST Wed


def test_eod_exit_fires_inside_the_closing_cushion():
    """NSE closes 15:30 IST = 10:00 UTC; the cushion is 15 minutes, so
    09:46 UTC (15:16 IST) fires and takes the whole position."""
    position = _position(segment=Segment.NSE_STOCK, opened_at=_NSE_MID)
    now = datetime.datetime(2026, 8, 5, 9, 46, tzinfo=datetime.UTC)
    result = session_close_exit_check(position, now)
    assert result is not None
    assert result.reason == "EOD_EXIT"
    assert result.qty_lots == 10


def test_eod_exit_does_not_fire_before_the_cushion():
    position = _position(segment=Segment.NSE_STOCK, opened_at=_NSE_MID)
    now = datetime.datetime(2026, 8, 5, 9, 44, tzinfo=datetime.UTC)
    assert session_close_exit_check(position, now) is None


def test_overnight_position_exits_on_the_next_tick_it_sees():
    """The straggler path. If the engine was down or an exit could not fill
    during the final minutes, the next morning's ticks are nowhere near a
    close — so the cushion check alone would hold it all the following day,
    which is the exact thing the intraday rule forbids."""
    opened = datetime.datetime(2026, 8, 5, 6, 0, tzinfo=datetime.UTC)  # Wed
    next_morning = datetime.datetime(2026, 8, 6, 4, 0, tzinfo=datetime.UTC)  # Thu 09:30 IST
    position = _position(segment=Segment.NSE_STOCK, opened_at=opened)
    result = session_close_exit_check(position, next_morning)
    assert result is not None
    assert result.reason == "OVERNIGHT_EXIT"
    assert result.qty_lots == 10


def test_eod_exit_outranks_the_partial_exit_that_would_strand_a_remainder():
    """The rule that makes the ordering load-bearing. At 15:16 IST this
    position is also through its 1R target, and `r_multiple_partial_exit_
    check` returns HALF the lots — so if it won the tick, five lots would
    be carried overnight. The whole position has to go."""
    position = _position(
        segment=Segment.NSE_STOCK,
        opened_at=_NSE_MID,
        current_mark=Decimal(120),  # r = 2.0, clears both R targets
        high_water_mark_price=Decimal(120),
    )
    assert r_multiple_partial_exit_check(position) is not None  # it really would have fired
    result = evaluate_exits(position, datetime.datetime(2026, 8, 5, 9, 46, tzinfo=datetime.UTC))
    assert result is not None
    assert result.reason == "EOD_EXIT"
    assert result.qty_lots == position.qty_lots  # all of it, not a fraction


def test_stop_loss_still_outranks_the_session_close():
    """Risk protection stays first: the reason recorded must be the one
    that actually fired, and a stop breach at 15:16 is still a stop."""
    position = _position(
        segment=Segment.NSE_STOCK, opened_at=_NSE_MID, current_mark=Decimal(90)
    )
    result = evaluate_exits(position, datetime.datetime(2026, 8, 5, 9, 46, tzinfo=datetime.UTC))
    assert result is not None
    assert result.reason == "STOP_LOSS"
