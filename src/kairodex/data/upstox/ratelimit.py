"""Upstox market-data rate limits: 50 req/s, 500 req/min, 2000 req/30min,
all enforced simultaneously (confirmed against current Upstox docs). Upstox
exposes no usage-query endpoint the way LSE does, so this limiter is also
the only source for the `quota()` port method — it reports our own
consumption against the documented ceilings, not a vendor-reported figure.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque

_WINDOWS = ((1.0, 50), (60.0, 500), (1800.0, 2000))  # (seconds, max_requests)


class RateLimiter:
    def __init__(self) -> None:
        self._hits: deque[float] = deque()

    async def acquire(self) -> None:
        while True:
            now = time.monotonic()
            while self._hits and now - self._hits[0] > _WINDOWS[-1][0]:
                self._hits.popleft()

            wait = 0.0
            for window_secs, limit in _WINDOWS:
                in_window = sum(1 for t in self._hits if now - t <= window_secs)
                if in_window >= limit:
                    oldest_in_window = next(t for t in self._hits if now - t <= window_secs)
                    wait = max(wait, window_secs - (now - oldest_in_window))

            if wait <= 0:
                self._hits.append(now)
                return
            await asyncio.sleep(wait)

    def used_pct(self) -> float:
        """Fraction of the tightest (shortest-window) limit currently used."""
        now = time.monotonic()
        window_secs, limit = _WINDOWS[0]
        in_window = sum(1 for t in self._hits if now - t <= window_secs)
        return in_window / limit
