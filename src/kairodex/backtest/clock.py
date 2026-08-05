"""`ReplayClock` (ARCHITECTURE.md §13/Principle 1: "one engine, two
clocks"). Same `kairodex.core.clock.Clock` Protocol as `LiveClock` —
`.now()` — so `kairodex.strategy`/`kairodex.risk`/`kairodex.engine` code
never has to know which one it's running under. Only `runner.py` ever
calls `advance_to`; nothing else should reach into a clock and move it.
"""

from __future__ import annotations

import datetime


class ReplayClock:
    def __init__(self, start: datetime.datetime) -> None:
        self._now = start

    def now(self) -> datetime.datetime:
        return self._now

    def advance_to(self, ts: datetime.datetime) -> None:
        if ts < self._now:
            raise ValueError(f"ReplayClock cannot move backward: {self._now} -> {ts}")
        self._now = ts
