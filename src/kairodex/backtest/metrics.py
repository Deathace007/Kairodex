"""Track A directional metrics (ARCHITECTURE.md §13's table) — pure
functions over `list[BacktestSignal]`, no DB, no I/O. Every metric skips
unresolved signals (`outcome is None`) rather than treating them as
losses; a signal with no forward bars to resolve against is missing
data, not a bad trade.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass

import numpy as np

from kairodex.backtest.types import BacktestSignal

_DEFAULT_BOOTSTRAP_SAMPLES = 2000


def _resolved(signals: list[BacktestSignal]) -> list[BacktestSignal]:
    return [s for s in signals if s.outcome is not None]


def hit_rate(signals: list[BacktestSignal]) -> float | None:
    """Fraction of resolved signals whose direction-adjusted return was
    positive by exit (TARGET, or TIME with the close still ahead of
    entry) — "did direction resolve correctly.\""""
    resolved = _resolved(signals)
    if not resolved:
        return None
    wins = sum(1 for s in resolved if s.outcome is not None and s.outcome.return_atr > 0)
    return wins / len(resolved)


def mfe_mae_ratio(signals: list[BacktestSignal]) -> float | None:
    """Aggregate `sum(mfe_atr) / sum(mae_atr)`, not a mean-of-per-signal-
    ratios — a single near-zero `mae_atr` would otherwise blow up the
    average; the aggregate answers "was the move asymmetric in our
    favour" as a population statement, which is what the gate needs."""
    resolved = _resolved(signals)
    if not resolved:
        return None
    total_mae = sum(s.outcome.mae_atr for s in resolved if s.outcome is not None)
    if total_mae == 0:
        return None
    total_mfe = sum(s.outcome.mfe_atr for s in resolved if s.outcome is not None)
    return total_mfe / total_mae


def expectancy_atr(signals: list[BacktestSignal]) -> float | None:
    resolved = _resolved(signals)
    if not resolved:
        return None
    return statistics.mean(s.outcome.return_atr for s in resolved if s.outcome is not None)


def expectancy_atr_ci95(
    signals: list[BacktestSignal],
    *,
    n_bootstrap: int = _DEFAULT_BOOTSTRAP_SAMPLES,
    seed: int | None = None,
) -> tuple[float, float] | None:
    """Bootstrap 95% CI on mean `return_atr` — the promotion gate's own
    "bootstrap 95% CI excludes zero" wording. `None` below 2 resolved
    signals (nothing to resample)."""
    resolved = _resolved(signals)
    if len(resolved) < 2:
        return None
    returns = np.array([s.outcome.return_atr for s in resolved if s.outcome is not None])
    rng = np.random.default_rng(seed)
    boot_means = rng.choice(returns, size=(n_bootstrap, len(returns)), replace=True).mean(axis=1)
    lo, hi = np.percentile(boot_means, [2.5, 97.5])
    return float(lo), float(hi)


def signal_lead_time(signals: list[BacktestSignal]) -> float | None:
    """Mean fraction of the [prior-move + forward-move] that occurred
    *after* the signal — SPEC_REVIEW C10's "Early Trade Detection," made
    measurable. Only over signals where a prior/forward move could both
    be computed (`lead_time_pct is not None`)."""
    values = [s.lead_time_pct for s in signals if s.lead_time_pct is not None]
    if not values:
        return None
    return statistics.mean(values)


def mfe_capture_ratio(signals: list[BacktestSignal]) -> float | None:
    """Mean `return_atr / mfe_atr` over resolved signals with a positive
    MFE — "of the best favourable move that was actually available, how
    much did we keep by exit." Unclamped: a TARGET exit is always <= 1.0
    (mfe includes the exit bar itself), a STOP exit after a real MFE
    correctly reads negative (the move reversed on us), a TIME exit
    somewhere in between."""
    resolved = _resolved(signals)
    ratios = [
        s.outcome.return_atr / s.outcome.mfe_atr
        for s in resolved
        if s.outcome is not None and s.outcome.mfe_atr > 0
    ]
    if not ratios:
        return None
    return statistics.mean(ratios)


def break_even_hit_rate(signals: list[BacktestSignal]) -> float | None:
    """The hit rate at which this strategy's *own* observed win/loss
    profile nets to zero — `1 / (1 + payoff_ratio)` where
    `payoff_ratio = mean(winning return_atr) / mean(|losing return_atr|)`,
    the standard break-even-win-rate identity. The promotion gate's
    "directional hit rate > break-even for the strategy's own MFE/MAE
    profile" (ARCHITECTURE.md §10) — `return_atr` already reflects each
    signal's realized MFE/MAE-bounded outcome, so this is that profile,
    not a fixed stop:target assumption. `None` without at least one
    win and one loss (no profile to compare against)."""
    resolved = _resolved(signals)
    outcomes = [s.outcome for s in resolved if s.outcome is not None]
    wins = [o.return_atr for o in outcomes if o.return_atr > 0]
    losses = [-o.return_atr for o in outcomes if o.return_atr < 0]
    if not wins or not losses:
        return None
    payoff_ratio = statistics.mean(wins) / statistics.mean(losses)
    return 1.0 / (1.0 + payoff_ratio)


def quarterly_expectancy(signals: list[BacktestSignal]) -> dict[str, float]:
    """Mean `return_atr` per calendar quarter (`"2026Q1"` etc.) — the
    promotion gate's "positive expectancy in >= 3 of 4 quarters," not
    curve-fit robustness."""
    resolved = _resolved(signals)
    buckets: dict[str, list[float]] = defaultdict(list)
    for s in resolved:
        assert s.outcome is not None
        quarter = (s.ts.month - 1) // 3 + 1
        buckets[f"{s.ts.year}Q{quarter}"].append(s.outcome.return_atr)
    return {k: statistics.mean(v) for k, v in buckets.items()}


def regime_expectancy(signals: list[BacktestSignal]) -> dict[str, float]:
    """Mean `return_atr` split by `volatility_regime` (>1.0 = expanding
    vs. <=1.0 = contracting, `features.compute.volatility`'s own ratio
    convention) — the gate's ">= 2 volatility regimes," satisfied by this
    binary split. Skips signals with no `vol_regime` recorded."""
    resolved = [s for s in _resolved(signals) if s.vol_regime is not None]
    buckets: dict[str, list[float]] = defaultdict(list)
    for s in resolved:
        assert s.outcome is not None and s.vol_regime is not None
        label = "expanding" if s.vol_regime > 1.0 else "contracting"
        buckets[label].append(s.outcome.return_atr)
    return {k: statistics.mean(v) for k, v in buckets.items()}


@dataclass(frozen=True, slots=True)
class DirectionalMetrics:
    n_signals: int
    n_resolved: int
    hit_rate: float | None
    break_even_hit_rate: float | None
    mfe_mae_ratio: float | None
    expectancy_atr: float | None
    expectancy_atr_ci95: tuple[float, float] | None
    signal_lead_time: float | None
    mfe_capture_ratio: float | None
    quarterly_expectancy: dict[str, float]
    regime_expectancy: dict[str, float]


def compute_metrics(
    signals: list[BacktestSignal], *, seed: int | None = None
) -> DirectionalMetrics:
    return DirectionalMetrics(
        n_signals=len(signals),
        n_resolved=len(_resolved(signals)),
        hit_rate=hit_rate(signals),
        break_even_hit_rate=break_even_hit_rate(signals),
        mfe_mae_ratio=mfe_mae_ratio(signals),
        expectancy_atr=expectancy_atr(signals),
        expectancy_atr_ci95=expectancy_atr_ci95(signals, seed=seed),
        signal_lead_time=signal_lead_time(signals),
        mfe_capture_ratio=mfe_capture_ratio(signals),
        quarterly_expectancy=quarterly_expectancy(signals),
        regime_expectancy=regime_expectancy(signals),
    )
