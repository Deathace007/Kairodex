import datetime
from decimal import Decimal

from kairodex.analytics import performance
from kairodex.analytics.types import EquityPoint, TradeRecord
from kairodex.core.enums import Segment

_TS = datetime.datetime(2026, 1, 15, 10, 0, tzinfo=datetime.UTC)


def _trade(
    *,
    net_pnl: Decimal | None,
    gross_pnl: Decimal | None = None,
    closed: bool = True,
    avg_entry: Decimal = Decimal(100),
    avg_exit: Decimal | None = Decimal(110),
    initial_stop_price: Decimal | None = Decimal(90),
    profit_target: Decimal | None = Decimal(120),
    holding_secs: int | None = 3600,
    fees: Decimal | None = Decimal(5),
) -> TradeRecord:
    return TradeRecord(
        trade_id=1,
        segment=Segment.NSE_STOCK,
        strategy_id=1,
        underlying_symbol="RELIANCE",
        instrument_symbol="RELIANCE26AUGCE",
        option_type="C",
        strike=Decimal(2900),
        expiry=datetime.date(2026, 8, 26),
        opened_at=_TS,
        closed_at=_TS + datetime.timedelta(seconds=holding_secs or 0) if closed else None,
        avg_entry=avg_entry,
        avg_exit=avg_exit if closed else None,
        initial_stop_price=initial_stop_price,
        profit_target=profit_target,
        gross_pnl=gross_pnl if gross_pnl is not None else net_pnl,
        net_pnl=net_pnl,
        fees=fees,
        holding_secs=holding_secs if closed else None,
        exit_reason="TARGET" if closed else None,
        context_entry={"underlying_px": 2950.0, "vol_regime": 1.2, "trend_state_strength": 0.02},
        mfe=Decimal(20),
        mae=Decimal(-5),
    )


def test_win_rate_counts_positive_net_pnl_only():
    trades = [_trade(net_pnl=Decimal(100)), _trade(net_pnl=Decimal(-50))]
    assert performance.win_rate(trades) == 0.5


def test_win_rate_none_with_no_closed_trades():
    assert performance.win_rate([_trade(net_pnl=None, closed=False)]) is None


def test_profit_factor_ratio():
    trades = [_trade(net_pnl=Decimal(200)), _trade(net_pnl=Decimal(-100))]
    assert performance.profit_factor(trades) == 2.0


def test_profit_factor_none_with_no_losses():
    assert performance.profit_factor([_trade(net_pnl=Decimal(100))]) is None


def test_r_multiple_is_a_pure_price_ratio():
    # entry 100, stop 90 (risk=10), exit 110 -> R = (110-100)/10 = 1.0
    t = _trade(net_pnl=Decimal(1), avg_entry=Decimal(100), avg_exit=Decimal(110),
               initial_stop_price=Decimal(90))
    assert t.r_multiple == 1.0


def test_r_multiple_same_ratio_at_a_different_price_scale():
    # TradeRecord carries no quantity field at all — `r_multiple` is
    # `(avg_exit - avg_entry) / (avg_entry - initial_stop_price)`, three
    # price columns and nothing else, so it cannot be quantity-dependent
    # by construction. This checks the *scale* of the prices doesn't
    # matter either: same 2R at a 10x price level.
    small = _trade(net_pnl=Decimal(1), avg_entry=Decimal(10), avg_exit=Decimal(30),
                    initial_stop_price=Decimal(0))
    large = _trade(net_pnl=Decimal(1), avg_entry=Decimal(1000), avg_exit=Decimal(3000),
                    initial_stop_price=Decimal(0))
    assert small.r_multiple == large.r_multiple == 2.0


def test_r_multiple_partial_exit_sequence_matches_a_single_full_exit():
    # The orchestrator computes avg_exit as a qty-weighted average across
    # every leg (kairodex.engine.orchestrator.run_exit_tick) — a real bug
    # there once made a multi-leg avg_exit come out `lot_size`x too large
    # (caught by a P5 subagent review, fixed in the same session). This
    # documents the invariant that fix restores: 5@110 then 5@130 must
    # resolve to the same R as a single 10@120 exit, since (110+130)/2
    # equals the single-price-weighted average 120.
    sequential_avg_exit = (Decimal(110) * 5 + Decimal(130) * 5) / 10
    a = _trade(net_pnl=Decimal(1), avg_entry=Decimal(100), avg_exit=sequential_avg_exit,
               initial_stop_price=Decimal(90))
    b = _trade(net_pnl=Decimal(1), avg_entry=Decimal(100), avg_exit=Decimal(120),
               initial_stop_price=Decimal(90))
    assert a.r_multiple == b.r_multiple == 2.0


def test_r_multiple_none_without_initial_stop():
    t = _trade(net_pnl=Decimal(1), initial_stop_price=None)
    assert t.r_multiple is None


def test_expectancy_is_mean_net_pnl_over_closed():
    trades = [_trade(net_pnl=Decimal(100)), _trade(net_pnl=Decimal(-40))]
    assert performance.expectancy(trades) == Decimal(30)


def test_summarize_open_and_closed_counts():
    trades = [_trade(net_pnl=Decimal(100)), _trade(net_pnl=None, closed=False)]
    summary = performance.summarize(trades)
    assert summary.n_trades == 2
    assert summary.n_open == 1
    assert summary.n_closed == 1


def test_equity_curve_stats_max_drawdown_and_return():
    points = [
        EquityPoint(
            ts=_TS, equity=Decimal(50000), high_water_mark=Decimal(50000),
            drawdown=Decimal(0), exposure=Decimal(0),
        ),
        EquityPoint(
            ts=_TS + datetime.timedelta(days=1), equity=Decimal(45000),
            high_water_mark=Decimal(50000), drawdown=Decimal("0.10"), exposure=Decimal(0),
        ),
        EquityPoint(
            ts=_TS + datetime.timedelta(days=2), equity=Decimal(55000),
            high_water_mark=Decimal(55000), drawdown=Decimal(0), exposure=Decimal(0),
        ),
    ]
    stats = performance.equity_curve_stats(points)
    assert stats.start_equity == Decimal(50000)
    assert stats.current_equity == Decimal(55000)
    assert stats.high_water_mark == Decimal(55000)
    assert stats.max_drawdown_pct == Decimal("0.10")
    assert stats.total_return_pct == Decimal("0.10")


def test_equity_curve_stats_empty():
    stats = performance.equity_curve_stats([])
    assert stats.n_points == 0
    assert stats.current_equity is None
