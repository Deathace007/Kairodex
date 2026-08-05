"""T0 tier (ARCHITECTURE.md §6): daily instrument-master sync and watchlist
seeding. Both are one-shot batch jobs, not part of the live ingest loop.
"""

from __future__ import annotations

import datetime
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kairodex.core.enums import Segment
from kairodex.data.ingest import upsert_instrument
from kairodex.data.ports import MarketDataProvider
from kairodex.store.models import Instrument, WatchlistMembership

logger = logging.getLogger(__name__)


async def sync_instruments(session: AsyncSession, client: MarketDataProvider, provider: str) -> int:
    """Upsert the full instrument master for one vendor. Returns row count."""
    count = 0
    async for rec in client.instruments():
        await upsert_instrument(session, rec, provider)
        count += 1
        if count % 500 == 0:
            await session.commit()
    await session.commit()
    return count


async def sync_watchlist(
    session: AsyncSession, segment: Segment, symbols: list[str], tier: int = 1
) -> tuple[list[str], list[str]]:
    """Seed `watchlist_membership` for `segment` from a plain-ticker list
    (config/watchlist.yaml). Matches against `instruments.symbol` within the
    segment's exchange — run `sync_instruments` first or every symbol misses.

    Returns (matched, missed) symbols; missed ones are logged, not raised —
    one bad ticker in a 20-name list shouldn't block seeding the other 19.
    """
    exchange = "NSE" if segment.market.value == "nse" else "US"
    today = datetime.date.today()
    matched: list[str] = []
    missed: list[str] = []

    for symbol in symbols:
        instrument = await session.scalar(
            select(Instrument).where(
                Instrument.exchange == exchange,
                Instrument.symbol == symbol,
                Instrument.expiry.is_(None),
            )
        )
        if instrument is None:
            # Vendor trading-symbol casing/spacing is only known from live
            # data (see docs/PROGRESS.md §6 gotcha #2 for the LSE precedent)
            # — fall back to a case-insensitive match before giving up.
            instrument = await session.scalar(
                select(Instrument).where(
                    Instrument.exchange == exchange,
                    Instrument.symbol.ilike(symbol),
                    Instrument.expiry.is_(None),
                )
            )
        if instrument is None:
            missed.append(symbol)
            logger.warning("watchlist symbol not found in instruments: %s (%s)", symbol, exchange)
            continue

        # An already-open membership (valid_to still infinity, from ANY
        # valid_from, not just today) means this symbol is already an
        # active member — leave it alone. Checking only `valid_from ==
        # today` (the original bug, caught live) meant re-running
        # sync-watchlist on a later day always inserted a second row
        # instead of recognizing the symbol was already active, since the
        # first row's `valid_to` was never closed — both rows then read
        # as "currently valid" together, and every downstream consumer of
        # `watchlist_instruments` (this includes the already-deployed
        # live engine, not just backtesting) evaluated/could double-enter
        # that underlying on every single tick.
        already_active = await session.scalar(
            select(WatchlistMembership).where(
                WatchlistMembership.segment == segment,
                WatchlistMembership.instrument_id == instrument.instrument_id,
                WatchlistMembership.valid_to == datetime.date.max,
            )
        )
        if already_active is None:
            session.add(
                WatchlistMembership(
                    segment=segment,
                    instrument_id=instrument.instrument_id,
                    valid_from=today,
                    tier=tier,
                )
            )
        matched.append(symbol)

    await session.commit()
    return matched, missed
