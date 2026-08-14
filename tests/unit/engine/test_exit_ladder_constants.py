"""The stop and the partial-exit rungs are ONE decision, not two.

`R` is defined as the stop distance, so every rung in
`_DEFAULT_R_MULTIPLE_TARGETS` is denominated in `_DEFAULT_STOP_LOSS_PCT`.
Changing the stop alone silently moves where profit is taken — which is
exactly the drift that made "tighter stop" measure worse than the live
ruleset in replay on 2026-08-14 (-5,297 vs -3,002) while the same stop
with the ladder widened measured better (+7,311).

Nothing pinned that coupling before this file. These tests fail loudly if
either constant moves without the other being reconsidered.
"""

import dataclasses
import datetime
from decimal import Decimal

from kairodex.core.enums import Segment, Side
from kairodex.engine.monitor import (
    Position,
    r_multiple_partial_exit_check,
    trailing_stop_check,
)
from kairodex.engine.orchestrator import (
    _DEFAULT_PROFIT_TARGET_PCT,
    _DEFAULT_R_MULTIPLE_TARGETS,
    _DEFAULT_STOP_LOSS_PCT,
)

_NOW = datetime.datetime(2026, 8, 5, 6, 0, tzinfo=datetime.UTC)
_ENTRY = Decimal("100")


def _position_at(mark: str) -> Position:
    """A position entered at 100 with the live stop, marked at `mark`."""
    stop = _ENTRY * (Decimal(1) - Decimal(str(_DEFAULT_STOP_LOSS_PCT)))
    return Position(
        trade_id=1,
        segment=Segment.NSE_STOCK,
        instrument_id=1,
        underlying_symbol="TCS",
        side=Side.BUY,
        qty_lots=10,
        lot_size=25,
        avg_entry=_ENTRY,
        opened_at=_NOW,
        stop_price=stop,
        initial_stop_price=stop,
        current_mark=Decimal(mark),
        high_water_mark_price=Decimal(mark),
        r_multiple_targets=_DEFAULT_R_MULTIPLE_TARGETS,
    )


def test_the_only_rung_fires_at_forty_percent_of_premium():
    """2R against a 20% stop is +40% of entry premium. Worked by hand:
    risk_per_lot = 100 - 80 = 20, so 2R = 100 + 2*20 = 140."""
    assert _DEFAULT_STOP_LOSS_PCT == 0.20
    assert _DEFAULT_R_MULTIPLE_TARGETS == (2.0,)

    rung_pct = max(_DEFAULT_R_MULTIPLE_TARGETS) * _DEFAULT_STOP_LOSS_PCT
    assert rung_pct == 0.40

    assert r_multiple_partial_exit_check(_position_at("139")) is None
    fired = r_multiple_partial_exit_check(_position_at("140"))
    assert fired is not None
    assert fired.reason == "PARTIAL_EXIT_R2"
    assert fired.qty_lots == 5  # half the position


def test_nothing_banks_profit_below_the_measured_separation_point():
    """Only 8 of the 24 trades closed since the 2026-08-11 reset ever
    reached +15%, 3 reached +30% and 1 reached +60%, while every winner
    reached at least +12.2%. The old 0.5R rung sold half the position at
    +15% — on precisely the third of trades carrying the whole book. No
    rung may sit below +30% of premium again without new evidence."""
    lowest_rung_pct = min(_DEFAULT_R_MULTIPLE_TARGETS) * _DEFAULT_STOP_LOSS_PCT
    assert lowest_rung_pct >= 0.30, (
        f"lowest partial-exit rung is at +{100 * lowest_rung_pct:.0f}% of premium; "
        "below +30% clips the winners that carry the book — see PROGRESS.md §19"
    )

    for mark in ("109", "115", "129"):
        assert r_multiple_partial_exit_check(_position_at(mark)) is None


def test_profit_target_still_sits_above_every_rung():
    """The full-exit target must stay above the last partial, or the
    ladder is unreachable — `evaluate_exits` checks the target first."""
    target_pct = _DEFAULT_PROFIT_TARGET_PCT
    highest_rung_pct = max(_DEFAULT_R_MULTIPLE_TARGETS) * _DEFAULT_STOP_LOSS_PCT
    assert target_pct > highest_rung_pct


def test_trailing_stop_gives_back_exactly_the_initial_risk_by_default():
    """The third member of the coupled set, added 2026-08-14.

    §19d moved `_DEFAULT_STOP_LOSS_PCT` 0.30 -> 0.20 and moved the R-rungs
    with it, on the reasoning that R *is* the stop distance. The trailing
    stop's give-back is the same decision and was left behind at a bare
    0.30 literal in `engine/monitor.py`, where nothing failed.

    It is now derived from the position's own initial stop rather than
    configured separately, so the two cannot disagree. Live cost of them
    disagreeing: with a 20% stop and a 30% trail the trail becomes binding
    above +14.3% MFE and is wider than the initial risk — trade 79 peaked
    +21.0% and exited -15.8%, trade 84 peaked +16.4% and exited -29.0%.
    """
    entry = Decimal("100")
    stop = entry * (Decimal(1) - Decimal(str(_DEFAULT_STOP_LOSS_PCT)))  # 80
    position = dataclasses.replace(
        _position_at("100"),
        avg_entry=entry,
        stop_price=stop,
        initial_stop_price=stop,
        high_water_mark_price=Decimal("200"),
        current_mark=Decimal("200"),
    )
    ratchet = trailing_stop_check(position)
    assert ratchet is not None
    # Give-back must equal the stop pct, not some independent literal.
    expected = Decimal("200") * (Decimal(1) - Decimal(str(_DEFAULT_STOP_LOSS_PCT)))
    assert ratchet.new_stop_price == expected


def test_trailing_stop_abstains_when_there_is_no_real_risk_distance():
    """An initial stop at or above entry leaves no risk to mirror, exactly
    as `Position.r_multiple` returns None in the same situation."""
    position = dataclasses.replace(
        _position_at("100"),
        avg_entry=Decimal("100"),
        stop_price=Decimal("100"),
        initial_stop_price=Decimal("100"),
        high_water_mark_price=Decimal("200"),
        current_mark=Decimal("200"),
    )
    assert trailing_stop_check(position) is None
