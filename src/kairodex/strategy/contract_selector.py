"""Contract selector (ARCHITECTURE.md §10's pipeline step between
confluence and sizing; §11's affordability constraint: "the contract
selector treats it as a constraint rather than a preference. In practice
it biases NSE toward slightly OTM strikes"). Turns a directional signal
into one actual option leg.

This is an options-*buying* platform — `direction` here is the same
overloaded `Side` as everywhere else (see `core.enums.Side`'s docstring):
BUY = bullish bias -> buy a call, SELL = bearish bias -> buy a put. The
resulting order is always `Side.BUY` (opening a long option), never a
sale/write.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from decimal import Decimal

from kairodex.core.enums import Side

_DEFAULT_MIN_DTE = 1
_DEFAULT_MAX_DTE = 45
_DEFAULT_TARGET_DELTA = 0.40  # ponytail: first-pass — a common "slightly
# OTM, cheaper premium, still meaningful directional exposure" choice;
# recalibrate once backtesting can measure what delta actually performs.


@dataclass(frozen=True, slots=True)
class ContractCandidate:
    instrument_id: int
    strike: Decimal
    option_type: str  # "C" | "P"
    expiry: datetime.date
    bid: Decimal
    ask: Decimal
    delta: Decimal | None = None


@dataclass(frozen=True, slots=True)
class SelectionResult:
    selected: ContractCandidate | None
    reason: str | None = None  # populated when selected is None

    @property
    def mid_price(self) -> Decimal | None:
        if self.selected is None:
            return None
        return (self.selected.bid + self.selected.ask) / 2


def select_contract(
    candidates: list[ContractCandidate],
    direction: Side,
    *,
    spot: Decimal,
    equity: Decimal,
    max_premium_pct: float,
    lot_size: int,
    as_of: datetime.date,
    min_dte: int = _DEFAULT_MIN_DTE,
    max_dte: int = _DEFAULT_MAX_DTE,
    target_delta: float = _DEFAULT_TARGET_DELTA,
) -> SelectionResult:
    option_type = "C" if direction is Side.BUY else "P"
    in_window = [
        c
        for c in candidates
        if c.option_type == option_type
        and c.bid > 0
        and c.ask > 0
        and c.ask >= c.bid
        and min_dte <= (c.expiry - as_of).days <= max_dte
    ]
    if not in_window:
        return SelectionResult(None, "NO_CANDIDATES_IN_EXPIRY_WINDOW")

    max_premium = Decimal(str(max_premium_pct)) * equity
    affordable = [c for c in in_window if ((c.bid + c.ask) / 2) * lot_size <= max_premium]
    if not affordable:
        return SelectionResult(None, "NO_AFFORDABLE_CONTRACT")

    def distance(c: ContractCandidate) -> float:
        if c.delta is not None:
            return abs(float(c.delta) - target_delta)
        # No vendor delta on this leg — fall back to normalized distance
        # from spot as a moneyness proxy.
        return abs(float(c.strike - spot)) / float(spot) if spot > 0 else float("inf")

    selected = min(affordable, key=distance)
    return SelectionResult(selected)
