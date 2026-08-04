"""Canonical, vendor-agnostic data-transfer types.

Every adapter (kairodex.data.upstox, kairodex.data.lse) speaks these types on its
public surface. Vendor-specific field names and quirks are translated in
each adapter's own module and never leak past it — that's what makes
MarketDataProvider (kairodex/data/ports.py) swappable per ARCHITECTURE.md §7.
"""

from __future__ import annotations

import datetime
import enum
from dataclasses import dataclass, field
from decimal import Decimal

from kairodex.core.enums import ExerciseStyle, InstrumentKind, Segment, Settlement


class Timeframe(enum.StrEnum):
    ONE_MIN = "1m"
    THREE_MIN = "3m"
    FIVE_MIN = "5m"
    FIFTEEN_MIN = "15m"
    THIRTY_MIN = "30m"
    ONE_HOUR = "1h"
    ONE_DAY = "1d"


class FeedMode(enum.StrEnum):
    """What a live subscription returns. Vendors expose more granular modes
    (Upstox: ltpc/full/option_greeks/full_d30) — each adapter maps its own
    modes onto this small, intent-based set."""

    LTP = "ltp"  # last traded price only
    QUOTE = "quote"  # + bid/ask, volume, OI
    FULL = "full"  # + Greeks/IV where the vendor provides them


@dataclass(frozen=True, slots=True)
class InstrumentRecord:
    """One row of a vendor's instrument master, pre-upsert."""

    exchange: str
    symbol: str
    kind: InstrumentKind
    currency: str
    provider_ids: dict[str, str]
    segment: Segment | None = None  # None for underlyings — see ARCHITECTURE.md §5.1
    underlying_symbol: str | None = None
    strike: Decimal | None = None
    option_type: str | None = None  # "C" | "P"
    expiry: datetime.date | None = None
    exercise_style: ExerciseStyle | None = None
    settlement: Settlement | None = None
    lot_size: int | None = None
    tick_size: Decimal | None = None


@dataclass(frozen=True, slots=True)
class Tick:
    """One live quote/greeks reading for a single instrument."""

    instrument_key: str  # vendor's native key, resolved to instrument_id downstream
    ts: datetime.datetime
    # A chain quote is meaningless without knowing what contract it prices —
    # these let a ChainSnapshot self-describe without a separate instrument-
    # master lookup (used by kairodex.data.ingest for the P0/P1 upsert path).
    strike: Decimal | None = None
    option_type: str | None = None  # "C" | "P"
    ltp: Decimal | None = None
    bid: Decimal | None = None
    ask: Decimal | None = None
    bid_sz: int | None = None
    ask_sz: int | None = None
    volume: int | None = None
    oi: int | None = None
    oi_change: int | None = None
    underlying_px: Decimal | None = None
    iv: Decimal | None = None
    delta: Decimal | None = None
    gamma: Decimal | None = None
    theta: Decimal | None = None
    vega: Decimal | None = None
    rho: Decimal | None = None
    vendor_iv: Decimal | None = None  # cross-check only — see ARCHITECTURE.md §8


@dataclass(frozen=True, slots=True)
class ChainSnapshot:
    """One atomic read of an option chain. `complete` and the count fields
    are what let the recorder distinguish a real chain from a partial one
    (ARCHITECTURE.md §7 — an incomplete chain is unusable for signals but
    still stored)."""

    underlying: str
    expiry: datetime.date
    ts: datetime.datetime
    quotes: list[Tick] = field(default_factory=list)
    expected_count: int | None = None
    latency_ms: int | None = None

    @property
    def contract_count(self) -> int:
        return len(self.quotes)

    @property
    def complete(self) -> bool:
        return self.expected_count is not None and self.contract_count >= self.expected_count


@dataclass(frozen=True, slots=True)
class Bar:
    ts: datetime.datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    oi: int | None = None


@dataclass(frozen=True, slots=True)
class QuotaStatus:
    """Vendor-reported usage headroom (ARCHITECTURE.md §7 — LSE exposes this
    via GET /vault/usage; Upstox via documented per-endpoint call limits)."""

    used_pct: float
    resets_at: datetime.datetime | None = None
    raw: dict[str, object] = field(default_factory=dict)
