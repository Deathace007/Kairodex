"""ExecutionPort (ARCHITECTURE.md §11/§12): "the ExecutionPort has exactly
two implementations, SimulatedBroker and ShadowLogger. No broker
order-placement credential exists anywhere in the codebase or config
schema." Both are here.

Deliberately stateless — no in-memory tracking of partial-fill attempt
counts across calls. This codebase's own principle (Principle 2, the
event log/DB as the source of truth, applied consistently) means "how
many times has this order been attempted" belongs in the DB (an
`orders`/`fills` row count the orchestrator reads back), not a Python
object's mutable state that a process restart would silently lose. The
caller passes `attempt` in; these classes are pure functions of their
arguments.
"""

from __future__ import annotations

import datetime
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from kairodex.core.enums import Side
from kairodex.execution.fills import compute_fill
from kairodex.execution.types import CostBreakdown, OrderRequest, QuoteSnapshot

CostModelFn = Callable[[Side, Decimal, int], CostBreakdown]  # (side, premium, qty) -> costs

_DEFAULT_MAX_ATTEMPTS = 3


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    filled_qty: int
    remaining_qty: int
    price: Decimal | None
    costs: CostBreakdown | None
    rejected: bool
    reject_reason: str | None
    expired: bool  # partial-fill attempts exhausted with qty still remaining
    spread_bps: float | None = None
    slippage_bps: float | None = None


class ExecutionPort(Protocol):
    async def execute(
        self,
        order: OrderRequest,
        quote: QuoteSnapshot,
        now: datetime.datetime,
        *,
        attempt: int,
    ) -> ExecutionResult: ...


class SimulatedBroker:
    """Allocates simulated capital for real (paper) trades — the caller
    debits/credits `AccountState`/`equity_snapshots` based on this
    result, this class only computes what *would* happen.

    `cost_model` is per-market (NSE vs. US) — pass
    `kairodex.execution.costs.compute_nse_costs`/`compute_us_costs`, or
    `None` to skip cost computation (e.g. a caller that only cares about
    the fill itself)."""

    def __init__(
        self,
        *,
        cost_model: CostModelFn | None = None,
        max_attempts: int = _DEFAULT_MAX_ATTEMPTS,
        **fill_kwargs: object,
    ) -> None:
        self._cost_model = cost_model
        self._max_attempts = max_attempts
        self._fill_kwargs = fill_kwargs

    async def execute(
        self,
        order: OrderRequest,
        quote: QuoteSnapshot,
        now: datetime.datetime,
        *,
        attempt: int,
    ) -> ExecutionResult:
        outcome = compute_fill(order.side, order.qty, quote, now, **self._fill_kwargs)  # type: ignore[arg-type]
        if outcome.rejected:
            expired = attempt >= self._max_attempts
            return ExecutionResult(
                0, order.qty, None, None, True, outcome.reject_reason, expired,
                spread_bps=outcome.spread_bps, slippage_bps=outcome.slippage_bps,
            )

        costs = None
        if self._cost_model is not None and outcome.price is not None:
            premium = outcome.price * outcome.filled_qty
            costs = self._cost_model(order.side, premium, outcome.filled_qty)

        remaining = order.qty - outcome.filled_qty
        expired = remaining > 0 and attempt >= self._max_attempts
        return ExecutionResult(
            outcome.filled_qty, remaining, outcome.price, costs, False, None, expired,
            spread_bps=outcome.spread_bps, slippage_bps=outcome.slippage_bps,
        )


class ShadowLogger:
    """Wraps `SimulatedBroker`: "computes the identical fill, writes the
    identical events, allocates zero capital" (§12, verbatim). The fill
    computation genuinely is identical — there's no separate shadow fill
    model to drift from the real one. "Allocates zero capital" is the
    caller's responsibility: a shadow trade's `ExecutionResult` must never
    be used to debit/credit a real `AccountState`/`equity_snapshots` row,
    only a shadow-tagged one. Nothing here enforces that mechanically;
    it's a call-site discipline, same as `paper_trades`' `run_id IS NULL`
    convention distinguishing live from backtest."""

    def __init__(self, broker: SimulatedBroker) -> None:
        self._broker = broker

    async def execute(
        self,
        order: OrderRequest,
        quote: QuoteSnapshot,
        now: datetime.datetime,
        *,
        attempt: int,
    ) -> ExecutionResult:
        return await self._broker.execute(order, quote, now, attempt=attempt)
