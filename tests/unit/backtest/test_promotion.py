import datetime
import itertools
from decimal import Decimal

from kairodex.backtest import promotion
from kairodex.backtest.types import BacktestSignal, ForwardOutcome
from kairodex.backtest.validation import walk_forward_splits
from kairodex.core.enums import Segment, Side, StrategyStatus

_BASE = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)


def _outcome(return_atr: float, reason: str = "TARGET") -> ForwardOutcome:
    mfe = max(return_atr, 0.1)
    mae = 0.5 if return_atr < 0 else 0.2
    return ForwardOutcome(
        exit_reason=reason,
        exit_price=Decimal(100),
        bars_held=2,
        mfe=Decimal(str(mfe)),
        mae=Decimal(str(mae)),
        mfe_atr=mfe,
        mae_atr=mae,
        return_atr=return_atr,
    )


def _signal(day_offset: int, return_atr: float, vol_regime: float) -> BacktestSignal:
    reason = "TARGET" if return_atr > 0 else "STOP"
    return BacktestSignal(
        ts=_BASE + datetime.timedelta(days=day_offset),
        segment=Segment.NSE_STOCK,
        underlying_symbol="RELIANCE",
        direction=Side.BUY,
        confidence=0.8,
        entry_price=Decimal(100),
        atr_at_entry=Decimal(2),
        outcome=_outcome(return_atr, reason),
        lead_time_pct=0.7,
        vol_regime=vol_regime,
    )


def _strong_signal_set() -> list[BacktestSignal]:
    """400 signals over ~4 quarters, mostly winners (3:1 payoff), split
    across both volatility regimes — engineered to clear every Track A
    gate."""
    signals = []
    for i in range(400):
        day = i  # spread across ~400 days -> 4+ quarters
        is_win = i % 4 != 0  # 75% hit rate, well above break-even for 3:1 payoff (need > 25%)
        return_atr = 3.0 if is_win else -1.0
        vol_regime = 1.5 if i % 2 == 0 else 0.7
        signals.append(_signal(day, return_atr, vol_regime))
    return signals


class TestIsValidTransition:
    def test_full_happy_path_is_valid(self):
        path = [
            StrategyStatus.DRAFT,
            StrategyStatus.BACKTESTED,
            StrategyStatus.VALIDATED,
            StrategyStatus.SHADOW,
            StrategyStatus.PAPER_SMALL,
            StrategyStatus.PAPER_FULL,
        ]
        for a, b in itertools.pairwise(path):
            assert promotion.is_valid_transition(a, b)

    def test_retired_reachable_from_shadow_paper_small_paper_full(self):
        statuses = (StrategyStatus.SHADOW, StrategyStatus.PAPER_SMALL, StrategyStatus.PAPER_FULL)
        for status in statuses:
            assert promotion.is_valid_transition(status, StrategyStatus.RETIRED)

    def test_cannot_skip_states(self):
        assert not promotion.is_valid_transition(StrategyStatus.DRAFT, StrategyStatus.VALIDATED)
        assert not promotion.is_valid_transition(StrategyStatus.DRAFT, StrategyStatus.SHADOW)

    def test_cannot_go_backward(self):
        assert not promotion.is_valid_transition(StrategyStatus.SHADOW, StrategyStatus.VALIDATED)

    def test_retired_is_terminal(self):
        assert not promotion.is_valid_transition(StrategyStatus.RETIRED, StrategyStatus.DRAFT)


class TestEvaluateTrackA:
    def test_strong_signal_set_passes_every_gate(self):
        signals = _strong_signal_set()
        folds = walk_forward_splits(signals, n_folds=4, embargo=datetime.timedelta(days=3))
        result = promotion.evaluate_track_a(signals, folds, n_trials=1, seed=7)
        failed = [c for c in result.checks if not c.passed]
        assert failed == [], f"unexpected failures: {failed}"
        assert result.passed

    def test_too_few_signals_fails_sample_size_only(self):
        signals = _strong_signal_set()[:50]
        folds = walk_forward_splits(signals, n_folds=4, embargo=datetime.timedelta(days=3))
        result = promotion.evaluate_track_a(signals, folds, n_trials=1, seed=7)
        sample_check = next(c for c in result.checks if c.name == "sample_size")
        assert not sample_check.passed
        assert not result.passed

    def test_losing_strategy_fails_expectancy_and_hit_rate(self):
        signals = [
            _signal(i, -1.0 if i % 4 != 0 else 3.0, 1.5 if i % 2 == 0 else 0.7)
            for i in range(400)
        ]  # 75% losers now
        folds = walk_forward_splits(signals, n_folds=4, embargo=datetime.timedelta(days=3))
        result = promotion.evaluate_track_a(signals, folds, n_trials=1, seed=7)
        by_name = {c.name: c for c in result.checks}
        assert not by_name["directional_hit_rate"].passed
        assert not by_name["expectancy"].passed
        assert not result.passed

    def test_more_trials_deflates_the_sharpe_gate_value(self):
        """The honest trial count actually changes the gate's outcome
        variable (not a static passthrough): a higher `n_trials` always
        deflates the reported Sharpe further, per
        `validation.deflated_sharpe`'s own monotonicity (already exercised
        directly in test_validation.py) — checked here through the gate
        wiring itself, using the value embedded in the check's detail."""
        signals = _strong_signal_set()
        folds = walk_forward_splits(signals, n_folds=4, embargo=datetime.timedelta(days=3))
        result_1_trial = promotion.evaluate_track_a(signals, folds, n_trials=1, seed=7)
        result_many_trials = promotion.evaluate_track_a(signals, folds, n_trials=10_000, seed=7)
        dsr_1 = next(c for c in result_1_trial.checks if c.name == "deflated_sharpe")
        dsr_many = next(c for c in result_many_trials.checks if c.name == "deflated_sharpe")
        value_1 = float(dsr_1.detail.split(" at ")[0])
        value_many = float(dsr_many.detail.split(" at ")[0])
        assert value_many < value_1

    def test_synthetic_overlay_negative_is_informational_not_a_gate(self):
        signals = _strong_signal_set()
        folds = walk_forward_splits(signals, n_folds=4, embargo=datetime.timedelta(days=3))
        result = promotion.evaluate_track_a(
            signals, folds, n_trials=1, seed=7, synthetic_overlay_negative=True
        )
        assert result.synthetic_overlay_requires_justification is True
        assert result.passed  # still passes — not a gate


def test_summarize_formats_pass_and_fail():
    checks = (
        promotion.GateCheck("a", True, "ok"),
        promotion.GateCheck("b", False, "not ok"),
    )
    text = promotion.summarize(checks)
    assert "PASS a: ok" in text
    assert "FAIL b: not ok" in text
