"""Promotion state machine + Track A gate checking (ARCHITECTURE.md §10).

    DRAFT -> BACKTESTED -> VALIDATED -> SHADOW -> PAPER_SMALL -> PAPER_FULL
                                          \\----------\\------------\\--> RETIRED

Track A (`evaluate_track_a`) is DB-free — it scores `list[BacktestSignal]`
against the doc's own gate table, same pure-function split as everywhere
else. Track B (live shadow economics, `SHADOW -> PAPER_SMALL`) reads real
`trades`/`fills`/`equity_snapshots` rows and belongs in
`kairodex.risk.accounting`'s DB-touching company, not here — see
`evaluate_track_b` there... — actually Track B is small enough to live
alongside Track A; see `evaluate_track_b` below, the one DB-touching
function in this file.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from decimal import Decimal

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kairodex.backtest import metrics
from kairodex.backtest.types import BacktestSignal
from kairodex.backtest.validation import WalkForwardFold, deflated_sharpe, walk_forward_efficiency
from kairodex.core.enums import Segment, is_valid_strategy_transition
from kairodex.store.models import EquitySnapshot, Fill, Order, Trade

_MIN_TRACK_A_SAMPLE = 200
_MIN_MFE_MAE_RATIO = 1.5
_MIN_SIGNAL_LEAD_TIME = 0.6
_MIN_QUARTERS_POSITIVE = 3
_MIN_QUARTERS_PRESENT = 4
_MIN_REGIMES_POSITIVE = 2
_MIN_WALK_FORWARD_EFFICIENCY = 0.5

_MIN_TRACK_B_SAMPLE = 100
_MIN_PROFIT_FACTOR = 1.3
_MAX_DRAWDOWN_PCT = 0.15
_ASSUMED_SLIPPAGE_TO_SPREAD_RATIO = 0.3  # = k/2 for fills.compute_fill's default k=0.6
_MAX_SLIPPAGE_REALISM_RATIO = 1.5  # ponytail: first-pass — realized/modelled
# slippage within 1.5x is "roughly what the model assumed"; no exact
# tolerance is given in the doc, recalibrate once real fills accumulate.

# The transition graph itself now lives in kairodex.core.enums (P6) — so
# kairodex.api's promote endpoint can validate a requested transition
# without importing this module (forbidden by the import-linter contract
# "API is glue, not business logic"). Re-exported here under its
# original name so every existing caller/test in this module keeps
# working unchanged.
is_valid_transition = is_valid_strategy_transition


@dataclass(frozen=True, slots=True)
class GateCheck:
    name: str
    passed: bool
    detail: str


@dataclass(frozen=True, slots=True)
class TrackAResult:
    checks: tuple[GateCheck, ...]
    synthetic_overlay_requires_justification: bool  # informational only — never a gate

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)


def evaluate_track_a(
    signals: list[BacktestSignal],
    folds: list[WalkForwardFold],
    *,
    n_trials: int,
    synthetic_overlay_negative: bool = False,
    seed: int | None = None,
) -> TrackAResult:
    """`BACKTESTED -> VALIDATED`'s gate table, scored literally. `folds`
    comes from `validation.walk_forward_splits` over the same `signals`;
    `n_trials` is the honest count of prior backtest runs for this
    strategy (deflated Sharpe's own deflator input) — the caller's to
    track (typically `count(backtest_runs) for this strategy_id`, see
    `runner.py`)."""
    m = metrics.compute_metrics(signals, seed=seed)
    checks = []

    checks.append(
        GateCheck(
            "sample_size",
            m.n_resolved >= _MIN_TRACK_A_SAMPLE,
            f"{m.n_resolved} resolved signals (need >= {_MIN_TRACK_A_SAMPLE})",
        )
    )

    hit_rate_ok = (
        m.hit_rate is not None
        and m.break_even_hit_rate is not None
        and m.hit_rate > m.break_even_hit_rate
    )
    checks.append(
        GateCheck(
            "directional_hit_rate",
            hit_rate_ok,
            f"hit_rate={m.hit_rate}, break_even={m.break_even_hit_rate}",
        )
    )

    expectancy_ok = (
        m.expectancy_atr is not None
        and m.expectancy_atr > 0
        and m.expectancy_atr_ci95 is not None
        and m.expectancy_atr_ci95[0] > 0
    )
    checks.append(
        GateCheck(
            "expectancy",
            expectancy_ok,
            f"expectancy_atr={m.expectancy_atr}, ci95={m.expectancy_atr_ci95}",
        )
    )

    mfe_mae_ok = m.mfe_mae_ratio is not None and m.mfe_mae_ratio >= _MIN_MFE_MAE_RATIO
    checks.append(
        GateCheck("mfe_mae_ratio", mfe_mae_ok, f"{m.mfe_mae_ratio} (need >= {_MIN_MFE_MAE_RATIO})")
    )

    lead_time_ok = m.signal_lead_time is not None and m.signal_lead_time >= _MIN_SIGNAL_LEAD_TIME
    checks.append(
        GateCheck(
            "signal_lead_time",
            lead_time_ok,
            f"{m.signal_lead_time} (need >= {_MIN_SIGNAL_LEAD_TIME})",
        )
    )

    quarters_positive = sum(1 for v in m.quarterly_expectancy.values() if v > 0)
    regimes_positive = sum(1 for v in m.regime_expectancy.values() if v > 0)
    consistency_ok = (
        len(m.quarterly_expectancy) >= _MIN_QUARTERS_PRESENT
        and quarters_positive >= _MIN_QUARTERS_POSITIVE
        and regimes_positive >= _MIN_REGIMES_POSITIVE
    )
    checks.append(
        GateCheck(
            "consistency",
            consistency_ok,
            f"{quarters_positive}/{len(m.quarterly_expectancy)} quarters positive "
            f"(need {_MIN_QUARTERS_POSITIVE}/{_MIN_QUARTERS_PRESENT}+), "
            f"{regimes_positive} regimes positive (need >= {_MIN_REGIMES_POSITIVE})",
        )
    )

    returns = [s.outcome.return_atr for s in signals if s.outcome is not None]
    dsr = deflated_sharpe(returns, n_trials=n_trials)
    dsr_ok = dsr is not None and dsr > 0
    checks.append(GateCheck("deflated_sharpe", dsr_ok, f"{dsr} at {n_trials} trials (need > 0)"))

    wfe = walk_forward_efficiency(folds)
    wfe_ok = wfe is not None and wfe >= _MIN_WALK_FORWARD_EFFICIENCY
    checks.append(
        GateCheck(
            "walk_forward_efficiency", wfe_ok, f"{wfe} (need >= {_MIN_WALK_FORWARD_EFFICIENCY})"
        )
    )

    return TrackAResult(
        checks=tuple(checks),
        synthetic_overlay_requires_justification=synthetic_overlay_negative,
    )


@dataclass(frozen=True, slots=True)
class TrackBResult:
    checks: tuple[GateCheck, ...]

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)


async def evaluate_track_b(
    session: AsyncSession,
    *,
    strategy_id: int,
    segment: Segment,
    segment_capital: Decimal,
    track_a_hit_rate: float | None,
) -> TrackBResult:
    """`SHADOW -> PAPER_SMALL`'s gate table, against real `trades`/
    `fills`/`equity_snapshots` rows (`run_id IS NULL` — live/shadow, never
    a backtest run). The one DB-touching function in this module, same
    split as `kairodex.risk.loader`."""
    closed = list(
        await session.scalars(
            select(Trade).where(
                Trade.strategy_id == strategy_id,
                Trade.run_id.is_(None),
                Trade.closed_at.is_not(None),
            )
        )
    )
    checks = []
    n = len(closed)

    ci_excludes_zero = False
    if n >= 2:
        returns = [float(t.net_pnl) for t in closed if t.net_pnl is not None]
        # Same bootstrap machinery as Track A's expectancy CI, over raw
        # net_pnl rather than ATR units — Track B measures real currency
        # economics, not direction.
        rng = np.random.default_rng()
        arr = np.array(returns)
        boot_means = rng.choice(arr, size=(2000, len(arr)), replace=True).mean(axis=1)
        lo = float(np.percentile(boot_means, 2.5))
        ci_excludes_zero = lo > 0
    sample_ok = n >= _MIN_TRACK_B_SAMPLE or ci_excludes_zero
    checks.append(
        GateCheck(
            "sample_size",
            sample_ok,
            f"{n} shadow trades (need >= {_MIN_TRACK_B_SAMPLE}, or a CI excluding zero)",
        )
    )

    wins = [float(t.net_pnl) for t in closed if t.net_pnl is not None and t.net_pnl > 0]
    losses = [float(t.net_pnl) for t in closed if t.net_pnl is not None and t.net_pnl < 0]
    profit_factor = (sum(wins) / abs(sum(losses))) if losses else (float("inf") if wins else None)
    pf_ok = profit_factor is not None and profit_factor >= _MIN_PROFIT_FACTOR
    checks.append(
        GateCheck("profit_factor", pf_ok, f"{profit_factor} (need >= {_MIN_PROFIT_FACTOR})")
    )

    max_dd_row = await session.scalar(
        select(EquitySnapshot)
        .where(EquitySnapshot.segment == segment, EquitySnapshot.run_id == 0)
        .order_by(EquitySnapshot.drawdown.desc())
        .limit(1)
    )
    max_dd = float(max_dd_row.drawdown) if max_dd_row is not None else None
    dd_ok = max_dd is not None and max_dd <= _MAX_DRAWDOWN_PCT
    checks.append(
        GateCheck("max_drawdown", dd_ok, f"{max_dd} (need <= {_MAX_DRAWDOWN_PCT})")
    )

    fills = list(
        await session.scalars(
            select(Fill)
            .join(Order, Order.order_id == Fill.order_id)
            .join(Trade, Trade.trade_id == Order.trade_id)
            .where(Trade.strategy_id == strategy_id, Trade.run_id.is_(None))
        )
    )
    # "Within tolerance of the model's assumption": fills.compute_fill's
    # default k=0.6 implies slippage = k*half_spread = 0.5*k of the full
    # spread -> slippage/spread ~= 0.3 modelled. Compare that to what
    # actually got recorded on real fills.
    ratio_pairs = [
        (abs(float(f.slippage_bps)), float(f.spread_bps))
        for f in fills
        if f.slippage_bps is not None and f.spread_bps is not None and f.spread_bps > 0
    ]
    slippage_ok = False
    slippage_detail = "0 fills with both spread_bps and slippage_bps recorded"
    if ratio_pairs:
        realized_ratio = statistics.mean(s / sp for s, sp in ratio_pairs)
        tolerance = _ASSUMED_SLIPPAGE_TO_SPREAD_RATIO * _MAX_SLIPPAGE_REALISM_RATIO
        slippage_ok = realized_ratio <= tolerance
        slippage_detail = (
            f"realized slippage/spread={realized_ratio:.4f} over {len(ratio_pairs)} fills "
            f"(modelled={_ASSUMED_SLIPPAGE_TO_SPREAD_RATIO}, tolerance={tolerance})"
        )
    checks.append(GateCheck("slippage_realism", slippage_ok, slippage_detail))

    shadow_hit_rate = (len(wins) / n) if n > 0 else None
    agreement_ok = False
    if shadow_hit_rate is not None and track_a_hit_rate is not None and n > 1:
        # 1 standard error of a binomial proportion at this sample size.
        se = math.sqrt(shadow_hit_rate * (1 - shadow_hit_rate) / n)
        agreement_ok = abs(shadow_hit_rate - track_a_hit_rate) <= se
    checks.append(
        GateCheck(
            "directional_agreement",
            agreement_ok,
            f"shadow_hit_rate={shadow_hit_rate}, track_a_hit_rate={track_a_hit_rate}",
        )
    )

    return TrackBResult(checks=tuple(checks))


def summarize(checks: tuple[GateCheck, ...]) -> str:
    lines = [f"{'PASS' if c.passed else 'FAIL'} {c.name}: {c.detail}" for c in checks]
    return "\n".join(lines)
