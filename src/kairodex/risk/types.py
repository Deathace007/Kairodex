"""Shared types for the risk gate chain (ARCHITECTURE.md §11)."""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from decimal import Decimal

from kairodex.core.enums import Segment, Side


@dataclass(frozen=True, slots=True)
class TradeProposal:
    """What a contract selector/sizer is asking the gate chain to approve
    — one specific contract, not just a directional signal. Liquidity
    fields are `None` when unavailable (e.g. thin chain) rather than a
    fabricated 0, so the liquidity gate can tell "known illiquid" apart
    from "unknown" and fail closed on either."""

    segment: Segment
    underlying_symbol: str
    direction: Side
    confidence: float
    ts: datetime.datetime
    premium_per_lot: Decimal
    lot_size: int
    stop_distance: Decimal  # premium terms — how much this trade risks per lot before stopping out
    liquidity_score: float | None = None
    spread_pct: float | None = None
    contract_oi: int | None = None
    contract_volume: int | None = None
    chain_complete: bool = True


@dataclass(frozen=True, slots=True)
class AccountState:
    """The gate chain's view of "where things stand" for one segment —
    built from `risk_state` + `equity_snapshots` + a live count over open
    `trades`, same separation as `kairodex.features`: this is plain data,
    built by a DB-touching loader elsewhere, so every gate is testable
    against a synthetic AccountState with no DB."""

    segment: Segment
    equity: Decimal
    high_water_mark: Decimal
    daily_pnl: Decimal
    weekly_pnl: Decimal
    consecutive_losses: int
    breaker_status: str  # "ARMED" | "TRIPPED"
    breaker_reason: str | None
    blocked_until: datetime.datetime | None
    kill_switch_engaged: bool
    open_positions_count: int
    open_underlyings: frozenset[str]
    total_exposure: Decimal  # sum of premium committed across open positions
    last_loss_ts_by_underlying: dict[str, datetime.datetime] = field(default_factory=dict)
    session_open: bool = True  # ARCHITECTURE.md §11's "session/time window" — see gates.py


@dataclass(frozen=True, slots=True)
class GateResult:
    gate: str
    allowed: bool
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class ChainResult:
    """The chain stops at the first denial — `evaluated` holds every gate
    that actually ran (i.e. up to and including the one that failed, or
    all of them if every gate passed), not the full 12 regardless."""

    allowed: bool
    evaluated: tuple[GateResult, ...]

    @property
    def reject_stage(self) -> str | None:
        return None if self.allowed else self.evaluated[-1].gate

    @property
    def reject_reason(self) -> str | None:
        return None if self.allowed else self.evaluated[-1].reason
