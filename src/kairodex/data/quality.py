"""Quality flagging (ARCHITECTURE.md §7): staleness, crossed book,
zero-volume, outlier, sequence gap -> a bitmask stored on every quote/bar
row. Poisoned rows are flagged and kept, never silently dropped — a
strategy or backtest can filter on the bitmask later, but the recorder's
job is to not lose data.

Pure functions only — no DB, no vendor knowledge, so this is testable
without a database or live credentials.
"""

from __future__ import annotations

import datetime
import enum
from decimal import Decimal

from kairodex.data.types import Tick


class QualityFlag(enum.IntFlag):
    NONE = 0
    STALE = 1 << 0  # tick timestamp is older than max_quote_age relative to now
    CROSSED_BOOK = 1 << 1  # bid > ask
    ZERO_VOLUME = 1 << 2  # traded volume is exactly zero
    OUTLIER = 1 << 3  # price jumped further than the sanity threshold vs. the prior tick
    SEQUENCE_GAP = 1 << 4  # elapsed time since the prior tick exceeds the expected cadence


DEFAULT_MAX_QUOTE_AGE = datetime.timedelta(seconds=15)
DEFAULT_MAX_PRICE_JUMP_PCT = Decimal("0.25")  # 25% tick-to-tick move is not "a quote", it's noise


def flag_tick(
    tick: Tick,
    *,
    now: datetime.datetime,
    prev_tick: Tick | None = None,
    max_quote_age: datetime.timedelta = DEFAULT_MAX_QUOTE_AGE,
    max_price_jump_pct: Decimal = DEFAULT_MAX_PRICE_JUMP_PCT,
    expected_interval: datetime.timedelta | None = None,
) -> int:
    """Bitmask for one tick. `prev_tick` and `expected_interval` are only
    used for the two flags that need history (OUTLIER, SEQUENCE_GAP) —
    both are None-able for a tick's first observation, where neither applies."""
    flags = QualityFlag.NONE

    if now - tick.ts > max_quote_age:
        flags |= QualityFlag.STALE

    if tick.bid is not None and tick.ask is not None and tick.bid > tick.ask:
        flags |= QualityFlag.CROSSED_BOOK

    if tick.volume is not None and tick.volume == 0:
        flags |= QualityFlag.ZERO_VOLUME

    if (
        prev_tick is not None
        and tick.ltp is not None
        and prev_tick.ltp is not None
        and prev_tick.ltp > 0
    ):
        jump = abs(tick.ltp - prev_tick.ltp) / prev_tick.ltp
        if jump > max_price_jump_pct:
            flags |= QualityFlag.OUTLIER

    if prev_tick is not None and expected_interval is not None:
        elapsed = tick.ts - prev_tick.ts
        if elapsed > expected_interval * 2:
            flags |= QualityFlag.SEQUENCE_GAP

    return int(flags)
