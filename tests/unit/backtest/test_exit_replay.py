"""The replay's own state machine — the one hand-written piece in
`backtest.exit_replay`, since the ladder itself is `monitor.evaluate_exits`
called directly. It drove a live config change (scratch 15 -> 40,
PROGRESS.md §28), so it gets a check that fails if the loop stops
threading stop/high-water/quantity state correctly between ticks.
"""

from __future__ import annotations

import datetime
from decimal import Decimal

from kairodex.backtest.exit_replay import TradeInput, Variant, replay

_OPEN = datetime.datetime(2026, 9, 16, 4, 30, tzinfo=datetime.UTC)  # 10:00 IST


def _trade(prices: list[float]) -> TradeInput:
    """One price per minute from 10:00 IST, entering at the first."""
    return TradeInput(
        trade_id=1,
        underlying_symbol="TEST",
        opened_at=_OPEN,
        session_date=datetime.date(2026, 9, 16),
        avg_entry=Decimal(str(prices[0])),
        lot_size=100,
        expiry=datetime.date(2026, 9, 29),
        path=tuple(
            (_OPEN + datetime.timedelta(minutes=i), Decimal(str(p))) for i, p in enumerate(prices)
        ),
    )


def test_scratch_window_decides_whether_a_flat_trade_is_cut():
    """A trade that never moves is cut at the window and held without it —
    the exact comparison the sweep was built to make."""
    flat = _trade([100.0] * 60)
    cut = replay(flat, Variant(scratch_minutes=15, runner_guard=False))
    held = replay(flat, Variant(scratch_minutes=None, runner_guard=False))
    assert cut.reasons[0] == "SCRATCH_EXIT"
    assert held.reasons[0] == "EOD_FORCED"
    # Same price either way, so the only difference is one extra hour of
    # nothing — both lose the spread, neither invents a gain.
    assert cut.net < 0 and held.net < 0


def test_breakeven_floor_catches_a_round_trip():
    """Up 20%, back to entry: the floor must exit at about entry rather
    than riding it down. This is the rule the sweep found had taken over
    the scratch rule's job."""
    out = replay(
        _trade([100.0] + [100.0 + 2 * i for i in range(10)] + [100.0] * 30),
        Variant(scratch_minutes=40, runner_guard=False),
    )
    assert out.reasons[0] == "BREAKEVEN_STOP"


def test_runner_guard_withholds_the_r_rung_on_a_single_lot():
    """The whole point of the guard: a 1-lot position past 2R must not be
    closed by PARTIAL_EXIT_R2, which on one lot is the entire position."""
    winner = _trade([100.0] + [100.0 + 4 * i for i in range(15)] + [160.0] * 20)
    without = replay(winner, Variant(scratch_minutes=40, runner_guard=False))
    with_guard = replay(winner, Variant(scratch_minutes=40, runner_guard=True))
    assert without.reasons[0] == "PARTIAL_EXIT_R2"
    assert "PARTIAL_EXIT_R2" not in with_guard.reasons


def test_stop_is_threaded_between_ticks_so_a_ratchet_persists():
    """A STOP_RATCHET returns no exit; if the loop dropped its
    `new_stop_price` the trailed level would reset every tick and the
    trade would run to the original stop instead."""
    out = replay(
        _trade([100.0] + [100.0 + 3 * i for i in range(12)] + [110.0, 100.0, 90.0]),
        Variant(scratch_minutes=40, runner_guard=True),
    )
    # Peaked at 133, so a 20% trail sits near 106 — far above the 80
    # initial stop. It must exit on the trail/floor, never STOP_LOSS.
    assert out.reasons[0] in {"TRAILING_STOP", "BREAKEVEN_STOP"}
    # A full stop on 100 units from a 100 entry is about -2,000. Exiting
    # near the trailed level costs a small fraction of that; the bound is
    # loose because the 0.9% exit slippage alone is ~100 on this size.
    assert out.net > -500
