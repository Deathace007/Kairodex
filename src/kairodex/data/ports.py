"""The MarketDataProvider port (ARCHITECTURE.md §7). Every vendor sits
behind this — swapping Upstox or LSE for another source is a new adapter
module, not a change to ingestion, features, or the engine."""

from __future__ import annotations

import datetime
from collections.abc import AsyncIterator
from typing import Protocol

from kairodex.data.types import (
    Bar,
    ChainSnapshot,
    FeedMode,
    InstrumentRecord,
    QuotaStatus,
    Tick,
    Timeframe,
)


class MarketDataProvider(Protocol):
    # Not `async def` — these are async *generators*: calling them returns the
    # iterator immediately (no await), consumed with `async for`. Declaring
    # them `async def` here would mean "a coroutine that resolves to an
    # iterator", which is a different, incompatible shape.
    def instruments(self) -> AsyncIterator[InstrumentRecord]:
        """Full instrument master for this vendor, as of now."""
        ...

    def subscribe(self, keys: list[str], mode: FeedMode) -> AsyncIterator[Tick]:
        """Live stream of quotes/greeks for the given vendor instrument keys."""
        ...

    async def chain(self, underlying: str, expiry: datetime.date) -> ChainSnapshot:
        """One atomic option-chain read for `underlying` at `expiry`."""
        ...

    async def list_expiries(self, underlying: str) -> list[datetime.date]:
        """Every future expiry currently listed for `underlying`, ascending.
        Used by the T1 recorder to pick "nearest 2 expiries" (ARCHITECTURE.md
        §6) without hardcoding or guessing a date."""
        ...

    async def bars(
        self, key: str, tf: Timeframe, start: datetime.date, end: datetime.date
    ) -> list[Bar]:
        """Historical underlying OHLCV — the backtest data source per the
        SPEC_REVIEW.md A3 decision (option-contract history is out of scope)."""
        ...

    async def quota(self) -> QuotaStatus:
        """Current usage headroom, for the quota-aware ingestion scheduler."""
        ...

    async def aclose(self) -> None:
        """Release any held connections (HTTP client, websocket)."""
        ...
