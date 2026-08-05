import datetime
from decimal import Decimal

from kairodex.backtest import metrics
from kairodex.backtest.types import BacktestSignal, ForwardOutcome
from kairodex.core.enums import Segment, Side

_TS = datetime.datetime(2026, 1, 15, tzinfo=datetime.UTC)


def _outcome(
    return_atr: float, mfe_atr: float, mae_atr: float, reason: str = "TARGET"
) -> ForwardOutcome:
    return ForwardOutcome(
        exit_reason=reason,
        exit_price=Decimal(100),
        bars_held=1,
        mfe=Decimal(str(mfe_atr)),
        mae=Decimal(str(mae_atr)),
        mfe_atr=mfe_atr,
        mae_atr=mae_atr,
        return_atr=return_atr,
    )


def _signal(
    ts: datetime.datetime = _TS,
    *,
    outcome: ForwardOutcome | None,
    lead_time_pct: float | None = None,
    vol_regime: float | None = None,
) -> BacktestSignal:
    return BacktestSignal(
        ts=ts,
        segment=Segment.NSE_STOCK,
        underlying_symbol="RELIANCE",
        direction=Side.BUY,
        confidence=0.8,
        entry_price=Decimal(100),
        atr_at_entry=Decimal(2),
        outcome=outcome,
        lead_time_pct=lead_time_pct,
        vol_regime=vol_regime,
    )


def test_hit_rate_counts_positive_returns_only():
    signals = [
        _signal(outcome=_outcome(2.0, 2.0, 0.1)),  # win
        _signal(outcome=_outcome(-1.0, 0.5, 1.0, "STOP")),  # loss
        _signal(outcome=_outcome(0.5, 0.5, 0.2)),  # win
        _signal(outcome=None),  # unresolved -> excluded from denominator
    ]
    assert metrics.hit_rate(signals) == 2 / 3


def test_hit_rate_none_when_nothing_resolved():
    assert metrics.hit_rate([_signal(outcome=None)]) is None


def test_mfe_mae_ratio_is_aggregate_not_mean_of_ratios():
    """sum(mfe_atr)=2.0+0.5=2.5, sum(mae_atr)=0.1+1.0=1.1 -> 2.5/1.1."""
    signals = [
        _signal(outcome=_outcome(2.0, 2.0, 0.1)),
        _signal(outcome=_outcome(-1.0, 0.5, 1.0, "STOP")),
    ]
    assert metrics.mfe_mae_ratio(signals) == 2.5 / 1.1


def test_mfe_mae_ratio_none_when_total_mae_zero():
    signals = [_signal(outcome=_outcome(2.0, 2.0, 0.0))]
    assert metrics.mfe_mae_ratio(signals) is None


def test_expectancy_atr_is_mean_of_return_atr():
    signals = [
        _signal(outcome=_outcome(2.0, 2.0, 0.1)),
        _signal(outcome=_outcome(-1.0, 0.5, 1.0, "STOP")),
        _signal(outcome=_outcome(0.5, 0.5, 0.2)),
    ]
    assert metrics.expectancy_atr(signals) == (2.0 - 1.0 + 0.5) / 3


def test_expectancy_ci95_none_below_two_resolved():
    assert metrics.expectancy_atr_ci95([_signal(outcome=_outcome(1.0, 1.0, 0.1))]) is None


def test_expectancy_ci95_brackets_the_mean_for_uniform_positive_returns():
    """All-positive returns with real spread -> both bounds should stay
    positive (an all-winning sample's bootstrap CI can't dip negative)."""
    signals = [_signal(outcome=_outcome(r, abs(r) + 0.1, 0.1)) for r in [0.5, 1.0, 1.5, 2.0, 2.5]]
    ci = metrics.expectancy_atr_ci95(signals, seed=42)
    assert ci is not None
    lo, hi = ci
    assert lo <= metrics.expectancy_atr(signals) <= hi
    assert lo > 0


def test_signal_lead_time_averages_only_defined_values():
    signals = [
        _signal(outcome=_outcome(1, 1, 0.1), lead_time_pct=0.8),
        _signal(outcome=_outcome(1, 1, 0.1), lead_time_pct=0.6),
        _signal(outcome=_outcome(1, 1, 0.1), lead_time_pct=None),
    ]
    assert metrics.signal_lead_time(signals) == (0.8 + 0.6) / 2


def test_mfe_capture_ratio_target_exit_is_bounded_by_one():
    """A TARGET exit's mfe_atr always >= return_atr (mfe includes the
    exit bar) -> capture ratio <= 1.0."""
    signals = [_signal(outcome=_outcome(2.0, 2.5, 0.3, "TARGET"))]
    ratio = metrics.mfe_capture_ratio(signals)
    assert ratio == 2.0 / 2.5
    assert ratio <= 1.0


def test_mfe_capture_ratio_negative_for_a_reversal_stop_out():
    """A real MFE existed (1.5) but the position reversed into a stop
    (-1.0) -> capture ratio is negative, correctly signalling "gave it
    all back and then some.\""""
    signals = [_signal(outcome=_outcome(-1.0, 1.5, 1.0, "STOP"))]
    assert metrics.mfe_capture_ratio(signals) == -1.0 / 1.5


def test_mfe_capture_ratio_skips_zero_mfe_signals():
    signals = [_signal(outcome=_outcome(-0.5, 0.0, 0.5, "STOP"))]
    assert metrics.mfe_capture_ratio(signals) is None


def test_break_even_hit_rate_hand_computed():
    """wins=[2.0, 4.0] -> mean=3.0. losses=[-1.0] -> |mean|=1.0.
    payoff_ratio = 3.0/1.0 = 3.0 -> break_even = 1/(1+3) = 0.25."""
    signals = [
        _signal(outcome=_outcome(2.0, 2.0, 0.1)),
        _signal(outcome=_outcome(4.0, 4.0, 0.1)),
        _signal(outcome=_outcome(-1.0, 0.5, 1.0, "STOP")),
    ]
    assert metrics.break_even_hit_rate(signals) == 0.25


def test_break_even_hit_rate_none_without_both_wins_and_losses():
    all_wins = [_signal(outcome=_outcome(1.0, 1.0, 0.1))]
    assert metrics.break_even_hit_rate(all_wins) is None
    all_losses = [_signal(outcome=_outcome(-1.0, 0.5, 1.0, "STOP"))]
    assert metrics.break_even_hit_rate(all_losses) is None


def test_quarterly_expectancy_buckets_by_calendar_quarter():
    q1 = datetime.datetime(2026, 2, 1, tzinfo=datetime.UTC)
    q3 = datetime.datetime(2026, 8, 1, tzinfo=datetime.UTC)
    signals = [
        _signal(q1, outcome=_outcome(1.0, 1.0, 0.1)),
        _signal(q1, outcome=_outcome(3.0, 3.0, 0.1)),
        _signal(q3, outcome=_outcome(-2.0, 0.5, 2.0, "STOP")),
    ]
    result = metrics.quarterly_expectancy(signals)
    assert result == {"2026Q1": 2.0, "2026Q3": -2.0}


def test_regime_expectancy_splits_expanding_vs_contracting():
    signals = [
        _signal(outcome=_outcome(1.0, 1.0, 0.1), vol_regime=1.5),  # expanding
        _signal(outcome=_outcome(2.0, 2.0, 0.1), vol_regime=1.2),  # expanding
        _signal(outcome=_outcome(-1.0, 0.5, 1.0, "STOP"), vol_regime=0.8),  # contracting
        _signal(outcome=_outcome(1.0, 1.0, 0.1), vol_regime=None),  # excluded, no regime
    ]
    result = metrics.regime_expectancy(signals)
    assert result == {"expanding": 1.5, "contracting": -1.0}


def test_compute_metrics_bundles_everything():
    signals = [
        _signal(outcome=_outcome(2.0, 2.0, 0.1), lead_time_pct=0.7, vol_regime=1.2),
        _signal(outcome=_outcome(-1.0, 0.5, 1.0, "STOP"), lead_time_pct=0.3, vol_regime=0.8),
    ]
    result = metrics.compute_metrics(signals, seed=1)
    assert result.n_signals == 2
    assert result.n_resolved == 2
    assert result.hit_rate == 0.5
    assert result.expectancy_atr == 0.5
    assert result.signal_lead_time == 0.5
    assert "expanding" in result.regime_expectancy
    assert "contracting" in result.regime_expectancy
