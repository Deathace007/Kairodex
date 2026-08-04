"""Small helpers shared by every vendor adapter. Vendor-specific field
mapping stays inside each adapter — this module only holds what both
genuinely need (ARCHITECTURE.md §7)."""

from __future__ import annotations

import datetime
from decimal import Decimal, InvalidOperation


def to_utc(dt: datetime.datetime) -> datetime.datetime:
    """Vendors return timestamps in their local exchange offset (Upstox:
    +05:30). Storage is always UTC (ARCHITECTURE.md §5)."""
    if dt.tzinfo is None:
        raise ValueError(f"naive datetime cannot be normalized to UTC: {dt!r}")
    return dt.astimezone(datetime.UTC)


def to_decimal(value: object) -> Decimal | None:
    """Safe numeric->Decimal conversion. Never route vendor numbers through
    float — see kairodex.core.money for why."""
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
