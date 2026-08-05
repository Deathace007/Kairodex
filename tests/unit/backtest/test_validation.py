import datetime
import math
from decimal import Decimal
from statistics import NormalDist

import pytest

from kairodex.backtest import validation
from kairodex.backtest.types import BacktestSignal, ForwardOutcome
from kairodex.core.enums import Segment, Side

_BASE = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)


def _outcome(return_atr: float, bars_held: int = 1) -> ForwardOutcome:
    return ForwardOutcome(
        exit_reason="TARGET",
        exit_price=Decimal(100),
        bars_held=bars_held,
        mfe=Decimal("1"),
        mae=Decimal("0.1"),
        mfe_atr=1.0,
        mae_atr=0.1,
        return_atr=return_atr,
    )


def _signal(day_offset: int, return_atr: float, bars_held: int = 1) -> BacktestSignal:
    return BacktestSignal(
        ts=_BASE + datetime.timedelta(days=day_offset),
        segment=Segment.NSE_STOCK,
        underlying_symbol="RELIANCE",
        direction=Side.BUY,
        confidence=0.8,
        entry_price=Decimal(100),
        atr_at_entry=Decimal(2),
        outcome=_outcome(return_atr, bars_held),
        lead_time_pct=None,
    )


def test_walk_forward_splits_rejects_fewer_than_two_folds():
    with pytest.raises(ValueError, match="n_folds"):
        validation.walk_forward_splits([], n_folds=1, embargo=datetime.timedelta(days=1))


def test_walk_forward_splits_empty_for_too_few_signals():
    assert validation.walk_forward_splits(
        [_signal(0, 1.0)], n_folds=2, embargo=datetime.timedelta(days=1)
    ) == []


def test_walk_forward_splits_produces_expanding_folds():
    """100 days of signals, one per day, 4 folds -> 3 usable folds (fold 0
    has no prior training data). Each fold's test set falls in its own
    quarter of the range, and every train signal's resolution window
    (1-day bars_held=1) ends at least the embargo before its fold's test
    starts."""
    signals = [_signal(d, 1.0) for d in range(100)]
    folds = validation.walk_forward_splits(signals, n_folds=4, embargo=datetime.timedelta(days=2))
    assert len(folds) == 3
    for fold in folds:
        assert fold.train
        assert fold.test
        assert all(s.ts < fold.test_start for s in fold.train)
        assert all(fold.test_start <= s.ts <= fold.test_end for s in fold.test)


def test_walk_forward_splits_purges_signals_too_close_to_test_start():
    """n_folds=2 over days 0-19 (span=19) -> test_start = day 9.5,
    purge_cutoff = 9.5 - embargo(1) = day 8.5. Two signals share ts=day 5:
    a normal one (bars_held=1, resolution_end=day6 <= 8.5 -> survives)
    and `bleeding` (bars_held=10, resolution_end=day15 > 8.5 -> purged) —
    same timestamp, different fate, purely from how far its own forward
    resolution window reaches past the embargo cutoff."""
    signals = [_signal(d, 1.0, bars_held=1) for d in range(20)]
    bleeding = _signal(5, 1.0, bars_held=10)
    signals = [*signals, bleeding]
    folds = validation.walk_forward_splits(signals, n_folds=2, embargo=datetime.timedelta(days=1))
    assert len(folds) == 1
    assert bleeding not in folds[0].train
    assert any(s.ts == bleeding.ts for s in folds[0].train)  # the short-duration twin survives


def test_walk_forward_efficiency_perfect_generalization_is_one():
    """Both folds have identical train/test expectancy (all signals
    return_atr=1.0) -> OOS/IS ratio = 1.0 in each -> mean = 1.0."""
    signals = [_signal(d, 1.0) for d in range(100)]
    folds = validation.walk_forward_splits(signals, n_folds=4, embargo=datetime.timedelta(days=1))
    assert validation.walk_forward_efficiency(folds) == pytest.approx(1.0)


def test_walk_forward_efficiency_none_when_no_positive_is_folds():
    signals = [_signal(d, -1.0) for d in range(100)]
    folds = validation.walk_forward_splits(signals, n_folds=4, embargo=datetime.timedelta(days=1))
    assert validation.walk_forward_efficiency(folds) is None


def test_deflated_sharpe_none_below_two_returns():
    assert validation.deflated_sharpe([1.0], n_trials=1) is None


def test_deflated_sharpe_none_for_zero_variance():
    assert validation.deflated_sharpe([1.0, 1.0, 1.0], n_trials=1) is None


def test_deflated_sharpe_single_trial_equals_raw_sharpe():
    """n_trials=1 -> no deflation (sr0=0) -> DSR == SR_hat exactly.
    returns=[1,2,3] -> mean=2, stdev=1 -> SR_hat=2.0."""
    result = validation.deflated_sharpe([1.0, 2.0, 3.0], n_trials=1)
    assert result == pytest.approx(2.0)


def test_deflated_sharpe_decreases_with_more_trials():
    """More trials -> a larger expected-max-under-noise deflator -> a
    lower (more conservative) deflated Sharpe for the identical return
    series."""
    returns = [0.5, 1.0, 1.5, 0.8, 1.2, 0.9, 1.1]
    dsr_1 = validation.deflated_sharpe(returns, n_trials=1)
    dsr_10 = validation.deflated_sharpe(returns, n_trials=10)
    dsr_100 = validation.deflated_sharpe(returns, n_trials=100)
    assert dsr_1 is not None and dsr_10 is not None and dsr_100 is not None
    assert dsr_1 > dsr_10 > dsr_100


def test_deflated_sharpe_hand_computed_two_trials():
    """returns=[1,2,3] (T=3) -> SR_hat=2.0 (mean=2, stdev=1). n_trials=2:
    e_max_z = (1-gamma)*Phi^-1(1-1/2) + gamma*Phi^-1(1-1/(2e))
            = (1-gamma)*Phi^-1(0.5) + gamma*Phi^-1(1 - 0.5/e)
    Phi^-1(0.5) = 0. Phi^-1(1 - 0.5/e) via NormalDist().inv_cdf.
    sr0 = e_max_z / sqrt(3). Verify against hand-recomputed formula."""
    gamma = 0.5772156649
    z = NormalDist()
    e_max_z = (1 - gamma) * z.inv_cdf(0.5) + gamma * z.inv_cdf(1 - 1 / (2 * math.e))
    expected_sr0 = e_max_z / math.sqrt(3)
    expected_dsr = 2.0 - expected_sr0
    result = validation.deflated_sharpe([1.0, 2.0, 3.0], n_trials=2)
    assert result == pytest.approx(expected_dsr)


def test_assert_not_touching_holdout_passes_when_range_ends_before_holdout():
    now = _BASE + datetime.timedelta(days=100)
    to_ts = _BASE + datetime.timedelta(days=50)  # well before the last 30 days
    validation.assert_not_touching_holdout(to_ts, now=now, holdout_days=30)  # no raise


def test_assert_not_touching_holdout_rejects_range_inside_holdout():
    now = _BASE + datetime.timedelta(days=100)
    to_ts = _BASE + datetime.timedelta(days=90)  # inside the last 30 days
    with pytest.raises(ValueError, match="held-out final period"):
        validation.assert_not_touching_holdout(to_ts, now=now, holdout_days=30)


def test_assert_not_touching_holdout_boundary_is_exclusive_of_holdout_start():
    now = _BASE + datetime.timedelta(days=100)
    holdout_start = now - datetime.timedelta(days=30)
    validation.assert_not_touching_holdout(holdout_start, now=now, holdout_days=30)  # no raise
    just_inside = holdout_start + datetime.timedelta(microseconds=1)
    with pytest.raises(ValueError):
        validation.assert_not_touching_holdout(just_inside, now=now, holdout_days=30)
