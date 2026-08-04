"""The Clock port (ARCHITECTURE.md Principle 1: one engine, two clocks).

Every timestamp the ingestion and engine code needs comes from a Clock,
never from datetime.now() directly — that's what lets the same orchestrator
run live and replayed later (ReplayClock arrives with the backtest engine
in P4; only LiveClock exists during ingestion-only P0/P1).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...


class LiveClock:
    def now(self) -> datetime:
        return datetime.now(UTC)
