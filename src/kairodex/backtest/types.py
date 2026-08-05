"""Shared, DB-free types for the backtest runner — same split as
everywhere else in this codebase (pure types/math vs. DB-touching
loaders)."""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from decimal import Decimal

from kairodex.core.enums import Segment, Side


@dataclass(frozen=True, slots=True)
class ForwardOutcome:
    """The result of walking forward from a signal's entry bar, per
    `resolve.resolve_forward_outcome` — Track A's stand-in for a real
    trade (ARCHITECTURE.md §13: option economics aren't in scope here,
    only "did direction resolve correctly")."""

    exit_reason: str  # "STOP" | "TARGET" | "TIME"
    exit_price: Decimal
    bars_held: int
    mfe: Decimal  # best favorable excursion, price units, >= 0
    mae: Decimal  # worst adverse excursion, price units, >= 0
    mfe_atr: float  # mfe / atr_at_entry
    mae_atr: float
    return_atr: float  # signed (direction-adjusted) return / atr_at_entry


@dataclass(frozen=True, slots=True)
class BacktestSignal:
    """One Track A signal: the confluence result plus its resolved
    forward outcome. `run_id` links back to `backtest_runs` once
    persisted; `None` while still in-memory during a run."""

    ts: datetime.datetime
    segment: Segment
    underlying_symbol: str
    direction: Side
    confidence: float
    entry_price: Decimal
    atr_at_entry: Decimal
    outcome: ForwardOutcome | None  # None if there weren't enough forward bars to resolve
    lead_time_pct: float | None  # fraction of the [prior + forward] move that came after the signal
    vol_regime: float | None = None  # features.compute.volatility.volatility_regime at signal time
