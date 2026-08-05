"""Rigour (ARCHITECTURE.md §13): purged/embargoed walk-forward splits,
deflated Sharpe, and the held-out final period guard. Pure functions
over `list[BacktestSignal]` / `list[float]` — no DB, no I/O.
"""

from __future__ import annotations

import datetime
import math
import statistics
from dataclasses import dataclass
from statistics import NormalDist

from kairodex.backtest import metrics
from kairodex.backtest.types import BacktestSignal

_EULER_MASCHERONI = 0.5772156649
_DEFAULT_HOLDOUT_DAYS = 30  # ponytail: first-pass, freely adjustable — no
# calendar-fraction formula given in the doc, this is a round default
# reserving the most recent month of history from every research run.


@dataclass(frozen=True, slots=True)
class WalkForwardFold:
    train: list[BacktestSignal]  # in-sample (IS)
    test: list[BacktestSignal]  # out-of-sample (OOS)
    test_start: datetime.datetime
    test_end: datetime.datetime


def _resolution_end(signal: BacktestSignal, bar_days: float) -> datetime.datetime:
    bars_held = signal.outcome.bars_held if signal.outcome is not None else 0
    return signal.ts + datetime.timedelta(days=bars_held * bar_days)


def walk_forward_splits(
    signals: list[BacktestSignal],
    *,
    n_folds: int,
    embargo: datetime.timedelta,
    bar_days: float = 1.0,
) -> list[WalkForwardFold]:
    """Expanding-window walk-forward over `n_folds` equal time slices —
    fold `i`'s train set is every signal before its test window whose own
    forward-resolution window (`ts + bars_held * bar_days`) finishes at
    least `embargo` before the test window starts (embargo >= max holding
    period, per the doc, so a trade straddling the boundary can't leak).
    Folds with an empty train or test set are dropped (nothing to learn
    from, or nothing to score)."""
    if n_folds < 2:
        raise ValueError(f"n_folds must be >= 2, got {n_folds}")
    ordered = sorted(signals, key=lambda s: s.ts)
    if len(ordered) < 2:
        return []
    start, end = ordered[0].ts, ordered[-1].ts
    span = end - start
    if span <= datetime.timedelta(0):
        return []
    fold_span = span / n_folds

    folds = []
    for i in range(1, n_folds):  # fold 0 has no prior data to train on
        test_start = start + fold_span * i
        test_end = end if i == n_folds - 1 else start + fold_span * (i + 1)
        purge_cutoff = test_start - embargo
        train = [
            s for s in ordered if s.ts < test_start and _resolution_end(s, bar_days) <= purge_cutoff
        ]
        test = [s for s in ordered if test_start <= s.ts <= test_end]
        if train and test:
            folds.append(
                WalkForwardFold(train=train, test=test, test_start=test_start, test_end=test_end)
            )
    return folds


def walk_forward_efficiency(folds: list[WalkForwardFold]) -> float | None:
    """Mean OOS/IS expectancy ratio across folds — the gate's "walk-forward
    efficiency >= 0.5." Folds where IS expectancy isn't positive are
    skipped (the ratio isn't meaningful when there was no in-sample edge
    to begin with)."""
    ratios = []
    for fold in folds:
        is_exp = metrics.expectancy_atr(fold.train)
        oos_exp = metrics.expectancy_atr(fold.test)
        if is_exp is not None and is_exp > 0 and oos_exp is not None:
            ratios.append(oos_exp / is_exp)
    if not ratios:
        return None
    return statistics.mean(ratios)


def deflated_sharpe(returns: list[float], *, n_trials: int) -> float | None:
    """Bailey & López de Prado's deflated Sharpe ratio (2014) — the
    Gaussian, non-skew/kurtosis-adjusted variant (a documented
    simplification: the full estimator's third/fourth-moment correction
    needs a larger sample to estimate reliably than this system's early
    backtests will have; upgrade once there's enough trade history to
    trust a skew/kurtosis estimate).

    Returns `SR_hat - SR0`: the observed (per-period) Sharpe ratio minus
    the expected maximum Sharpe achievable from `n_trials` independent
    trials of pure noise (the extreme-value-theory approximation for the
    max of `n_trials` standard normals, scaled into Sharpe units by
    `1/sqrt(T)`). "Deflated Sharpe > 0" (ARCHITECTURE.md §13's own gate
    wording) means this returned value, not a probability."""
    if len(returns) < 2 or n_trials < 1:
        return None
    stdev = statistics.stdev(returns)
    if stdev == 0:
        return None
    sr_hat = statistics.mean(returns) / stdev
    t = len(returns)

    if n_trials == 1:
        sr0 = 0.0
    else:
        z = NormalDist()
        term1 = (1 - _EULER_MASCHERONI) * z.inv_cdf(1 - 1 / n_trials)
        term2 = _EULER_MASCHERONI * z.inv_cdf(1 - 1 / (n_trials * math.e))
        sr0 = (term1 + term2) / math.sqrt(t)
    return sr_hat - sr0


def assert_not_touching_holdout(
    to_ts: datetime.datetime,
    *,
    now: datetime.datetime,
    holdout_days: int = _DEFAULT_HOLDOUT_DAYS,
) -> None:
    """The config guard ARCHITECTURE.md §13 calls for: "a held-out final
    period no research run may touch." Raises if a run's own `to_ts`
    reaches into the most recent `holdout_days` — call this before running
    anything, not after, so a violating run never executes at all."""
    holdout_start = now - datetime.timedelta(days=holdout_days)
    if to_ts > holdout_start:
        raise ValueError(
            f"backtest range extends into the held-out final period "
            f"(starts {holdout_start.isoformat()}, requested to_ts {to_ts.isoformat()}) "
            f"— no research run may touch it"
        )
