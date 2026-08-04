"""Exchange timezones. All storage is UTC (ARCHITECTURE.md §5) — these exist
only for session-boundary math and display."""

from __future__ import annotations

from zoneinfo import ZoneInfo

from kairodex.core.enums import Market

EXCHANGE_TZ: dict[Market, ZoneInfo] = {
    Market.NSE: ZoneInfo("Asia/Kolkata"),
    Market.US: ZoneInfo("America/New_York"),
}


def exchange_tz(market: Market) -> ZoneInfo:
    return EXCHANGE_TZ[market]
