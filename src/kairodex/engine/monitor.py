"""Position monitor / exit rules (ARCHITECTURE.md §11's controls list:
"auto stop-loss · trailing stop · partial exits at R-multiples · profit
targets · time-based exit (theta guard) · event-based exit"), and the
`Position`/`ExitDecision` types `Strategy.manage()` (§10) was left
without in the P3 strategy-framework session.

Every check is a pure function of `Position` (+ `now`/`blackout_active`
where relevant) — this codebase's usual split: DB-touching state
(current mark price, whether an event blackout is active right now)
belongs to whoever builds the `Position`, not to the check itself.

Priority order, evaluated by `evaluate_exits`, first match wins: risk
protection (stop-loss, trailing stop) before mandatory exits (time,
event) before opportunistic ones (profit target, partial R-multiple
exits) — if a position is simultaneously past its stop AND past a
profit target (a real possibility on a fast-moving tick), risk
protection is not optional the way profit-taking is.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from decimal import Decimal

from kairodex.core.enums import Segment, Side


@dataclass(frozen=True, slots=True)
class Position:
    trade_id: int
    segment: Segment
    instrument_id: int
    underlying_symbol: str
    side: Side  # always BUY on this options-buying platform
    qty_lots: int
    lot_size: int
    avg_entry: Decimal
    opened_at: datetime.datetime
    stop_price: Decimal
    initial_stop_price: Decimal  # fixed at entry, for R-multiple math even after the stop trails
    current_mark: Decimal
    high_water_mark_price: Decimal  # best mark seen since entry, for the trailing stop
    profit_target: Decimal | None = None
    r_multiple_targets: tuple[float, ...] = ()  # e.g. (1.0, 2.0) for partial exits
    partial_exits_taken: frozenset[float] = field(default_factory=frozenset)
    max_holding_secs: int | None = None  # theta guard

    @property
    def r_multiple(self) -> float | None:
        """(current gain per lot) / (initial risk per lot). None if the
        initial stop was set at (or above) entry — R is undefined without
        a real risk distance to divide by."""
        risk_per_lot = self.avg_entry - self.initial_stop_price
        if risk_per_lot <= 0:
            return None
        return float((self.current_mark - self.avg_entry) / risk_per_lot)


@dataclass(frozen=True, slots=True)
class ExitDecision:
    reason: str
    qty_lots: int  # 0 means no actual exit — a STOP_RATCHET update only, see trailing_stop_check
    new_stop_price: Decimal | None = None  # set on a trailing-stop update, even without an exit


def stop_loss_check(position: Position) -> ExitDecision | None:
    if position.current_mark <= position.stop_price:
        return ExitDecision("STOP_LOSS", position.qty_lots)
    return None


def trailing_stop_check(position: Position, *, trail_pct: float = 0.30) -> ExitDecision | None:
    """Stop trails at `trail_pct` below the best mark seen since entry —
    only ever moves up (never loosens), and only actually fires an exit
    if the CURRENT mark has fallen back down through the trailed level
    (not the position's originally-set `stop_price`, which the caller is
    responsible for having already ratcheted up over time — this
    function reports where the stop *should* be as of the latest high,
    and separately whether that new level is breached right now)."""
    trailed_stop = position.high_water_mark_price * Decimal(str(1 - trail_pct))
    effective_stop = max(position.stop_price, trailed_stop)
    if position.current_mark <= effective_stop:
        return ExitDecision("TRAILING_STOP", position.qty_lots, new_stop_price=effective_stop)
    if effective_stop > position.stop_price:
        # no exit, just report the stop should move up
        return ExitDecision("STOP_RATCHET", 0, new_stop_price=effective_stop)
    return None


def profit_target_check(position: Position) -> ExitDecision | None:
    if position.profit_target is not None and position.current_mark >= position.profit_target:
        return ExitDecision("PROFIT_TARGET", position.qty_lots)
    return None


def r_multiple_partial_exit_check(
    position: Position, *, exit_fraction: float = 0.5
) -> ExitDecision | None:
    """At each configured R-multiple target not yet taken, in ascending
    order, exit `exit_fraction` of the *current* remaining position
    (not the original size — a second partial exit at a higher R-multiple
    is a fraction of what's left after the first one, not double-counted
    against the original size)."""
    r = position.r_multiple
    if r is None:
        return None
    for target in sorted(position.r_multiple_targets):
        if target in position.partial_exits_taken:
            continue
        if r >= target:
            qty = max(1, round(position.qty_lots * exit_fraction))
            qty = min(qty, position.qty_lots)
            return ExitDecision(f"PARTIAL_EXIT_R{target:g}", qty)
    return None


def time_exit_check(position: Position, now: datetime.datetime) -> ExitDecision | None:
    if position.max_holding_secs is None:
        return None
    held_secs = (now - position.opened_at).total_seconds()
    if held_secs >= position.max_holding_secs:
        return ExitDecision("TIME_EXIT", position.qty_lots)
    return None


def event_exit_check(position: Position, *, blackout_active: bool) -> ExitDecision | None:
    """`blackout_active` is the caller's to determine (same real gap as
    `kairodex.risk.gates.event_blackout_gate`: no earnings/macro calendar
    integration exists yet) — this function's job is just what to do once
    that's known, not how to know it."""
    if blackout_active:
        return ExitDecision("EVENT_EXIT", position.qty_lots)
    return None


def evaluate_exits(
    position: Position,
    now: datetime.datetime,
    *,
    blackout_active: bool = False,
    trail_pct: float = 0.30,
    partial_exit_fraction: float = 0.5,
) -> ExitDecision | None:
    """First match wins, in priority order: stop-loss, trailing stop,
    time exit, event exit, profit target, R-multiple partial exit."""
    checks: list[ExitDecision | None] = [
        stop_loss_check(position),
        trailing_stop_check(position, trail_pct=trail_pct),
        time_exit_check(position, now),
        event_exit_check(position, blackout_active=blackout_active),
        profit_target_check(position),
        r_multiple_partial_exit_check(position, exit_fraction=partial_exit_fraction),
    ]
    for result in checks:
        if result is not None:
            return result
    return None
