"""Historical underlying OHLCV backfill (ARCHITECTURE.md §13's data
sources: Upstox candles v3 for NSE, LSE vault for US equities to 2003 —
both free, both already implemented behind `MarketDataProvider.bars()`
for P1's restart-recovery gap-fill). DB + vendor touching, one shot per
instrument — same split as `kairodex.data.recorder`.
"""

from __future__ import annotations

import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from kairodex.data.ingest import write_underlying_bars_batch
from kairodex.data.ports import MarketDataProvider
from kairodex.data.types import Timeframe
from kairodex.store.models import Instrument


async def backfill_underlying_history(
    session: AsyncSession,
    client: MarketDataProvider,
    provider: str,
    instrument: Instrument,
    *,
    timeframe: Timeframe = Timeframe.ONE_DAY,
    start: datetime.date,
    end: datetime.date,
) -> int:
    """Pulls `[start, end]` daily bars for one instrument and upserts
    them into `underlying_bars` (dedupe-on-conflict, same as every other
    ingest path). Returns 0, not an error, when this instrument has no
    key for `provider` yet — a caller backfilling a whole watchlist
    shouldn't die on one un-synced symbol."""
    # provider_ids is JSONB (dict[str, object]) since it can hold
    # non-string vendor ids in principle — every adapter in this codebase
    # only ever puts strings in it (same narrowing kairodex.data.recorder's
    # own `_provider_key` does), so this is a cast, not a real check.
    raw_key = (instrument.provider_ids or {}).get(provider)
    if raw_key is None:
        return 0
    key = str(raw_key)
    bars = await client.bars(key, timeframe, start, end)
    return await write_underlying_bars_batch(
        session, instrument.instrument_id, timeframe.value, provider, bars
    )
