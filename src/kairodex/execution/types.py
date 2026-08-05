"""Shared types for the execution simulator (ARCHITECTURE.md §12)."""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from decimal import Decimal

from kairodex.core.enums import Side


@dataclass(frozen=True, slots=True)
class OrderRequest:
    trade_id: int
    instrument_id: int
    side: Side
    qty: int  # contracts/lots requested
    order_type: str = "MARKET"
    limit_price: Decimal | None = None


@dataclass(frozen=True, slots=True)
class QuoteSnapshot:
    """What the simulator needs to know about the market at fill time —
    a plain snapshot, not a live DB read, so fill logic stays pure and
    testable (same separation as everywhere else in this codebase)."""

    bid: Decimal
    ask: Decimal
    bid_sz: int
    ask_sz: int
    quote_ts: datetime.datetime
    oi: int | None = None
    chain_complete: bool = True


@dataclass(frozen=True, slots=True)
class FillOutcome:
    filled_qty: int
    price: Decimal | None
    spread_bps: float | None
    slippage_bps: float | None
    rejected: bool
    reject_reason: str | None = None


@dataclass(frozen=True, slots=True)
class CostBreakdown:
    """Every line item, not just the total — so a trade record can show
    exactly what ate into P&L (ARCHITECTURE.md §12's "verify against a
    real contract note" only means anything if each line is inspectable
    on its own)."""

    brokerage: Decimal
    regulatory_fees: Decimal  # STT/exchange charges/SEBI fee (NSE) or OCC/ORF/SEC/TAF (US)
    taxes: Decimal  # GST (NSE) or none (US)

    @property
    def total(self) -> Decimal:
        return self.brokerage + self.regulatory_fees + self.taxes
