import datetime
from decimal import Decimal

from kairodex.backtest.synthetic_options import price_synthetic_overlay
from kairodex.backtest.types import BacktestSignal, ForwardOutcome
from kairodex.core.enums import Segment, Side
from kairodex.pricing import black76

_TS = datetime.datetime(2026, 1, 15, tzinfo=datetime.UTC)


def _signal(
    direction: Side, entry: float, exit_price: float, bars_held: int, reason: str = "TARGET"
) -> BacktestSignal:
    outcome = ForwardOutcome(
        exit_reason=reason,
        exit_price=Decimal(str(exit_price)),
        bars_held=bars_held,
        mfe=Decimal("1"),
        mae=Decimal("0.1"),
        mfe_atr=1.0,
        mae_atr=0.1,
        return_atr=1.0,
    )
    return BacktestSignal(
        ts=_TS,
        segment=Segment.NSE_STOCK,
        underlying_symbol="RELIANCE",
        direction=direction,
        confidence=0.8,
        entry_price=Decimal(str(entry)),
        atr_at_entry=Decimal(2),
        outcome=outcome,
        lead_time_pct=None,
    )


def test_none_for_unresolved_signal():
    unresolved = BacktestSignal(
        ts=_TS,
        segment=Segment.NSE_STOCK,
        underlying_symbol="RELIANCE",
        direction=Side.BUY,
        confidence=0.8,
        entry_price=Decimal(100),
        atr_at_entry=Decimal(2),
        outcome=None,
        lead_time_pct=None,
    )
    assert price_synthetic_overlay(unresolved, iv=0.2) is None


def test_none_for_non_positive_iv():
    signal = _signal(Side.BUY, 100, 110, 5)
    assert price_synthetic_overlay(signal, iv=0.0) is None
    assert price_synthetic_overlay(signal, iv=-0.1) is None


def test_call_pnl_positive_when_underlying_rises():
    signal = _signal(Side.BUY, 100, 110, 5)
    out = price_synthetic_overlay(signal, iv=0.25)
    assert out is not None
    assert out.exit_price > out.entry_price
    assert out.pnl_pct > 0


def test_put_pnl_positive_when_underlying_falls():
    signal = _signal(Side.SELL, 100, 90, 5)
    out = price_synthetic_overlay(signal, iv=0.25)
    assert out is not None
    assert out.exit_price > out.entry_price
    assert out.pnl_pct > 0


def test_matches_black76_price_directly():
    """Cross-check against calling black76.price with the exact same
    (flag, f, k, t, r, sigma) this module derives — entry: ATM call,
    t=30/365.25, f=k=100. Exit: f=110, t=(30-5)/365.25."""
    signal = _signal(Side.BUY, 100, 110, 5)
    out = price_synthetic_overlay(signal, iv=0.25, r=0.0)
    assert out is not None
    t_entry = 30.0 / 365.25
    t_exit = 25.0 / 365.25
    expected_entry = black76.price("c", 100.0, 100.0, t_entry, 0.0, 0.25)
    expected_exit = black76.price("c", 110.0, 100.0, t_exit, 0.0, 0.25)
    assert out.entry_price == expected_entry
    assert out.exit_price == expected_exit
    assert out.pnl_pct == (expected_exit - expected_entry) / expected_entry


def test_theta_decay_hurts_when_underlying_unchanged():
    """Same price at exit as entry (a TIME-out with no net move) still
    loses value purely from time decay: t_exit < t_entry -> exit_price <
    entry_price, pnl_pct < 0 — "is the move big enough to beat theta,"
    demonstrated by the negative case."""
    signal = _signal(Side.BUY, 100, 100, 10, reason="TIME")
    out = price_synthetic_overlay(signal, iv=0.25)
    assert out is not None
    assert out.exit_price < out.entry_price
    assert out.pnl_pct < 0
