"""Shared FastAPI dependencies — the DB session, and the tiny bits of
cross-cutting logic (currency conversion) every router would otherwise
duplicate.
"""

from __future__ import annotations

import datetime
from collections.abc import AsyncGenerator
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kairodex.store.base import get_sessionmaker
from kairodex.store.models import FxRate


async def get_session() -> AsyncGenerator[AsyncSession]:
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        yield session


async def convert(
    session: AsyncSession, amount: Decimal, from_ccy: str, to_ccy: str
) -> Decimal | None:
    """Latest `fx_rates` row for `{from_ccy}{to_ccy}` — `None` (not an
    error, not a fabricated 1:1) when no rate has ever been recorded,
    which is true of every pair today (`fx_rates` has no populating code
    path yet, same "known gap" class as P2 §8c's other deferred items).
    Callers degrade to reporting native-currency figures instead."""
    if from_ccy == to_ccy:
        return amount
    pair = f"{from_ccy}{to_ccy}"
    rate_row = await session.scalar(
        select(FxRate).where(FxRate.pair == pair).order_by(FxRate.as_of.desc()).limit(1)
    )
    if rate_row is None:
        return None
    return amount * rate_row.rate


def parse_window(window: str | None) -> tuple[datetime.datetime, datetime.datetime]:
    """`?window=7d|30d|90d|all` -> a `[frm, to)` pair ending now.
    `all` returns from the epoch, which is fine — every query here is
    already indexed on the relevant timestamp column."""
    to = datetime.datetime.now(datetime.UTC)
    if window is None or window == "30d":
        return to - datetime.timedelta(days=30), to
    if window == "all":
        return datetime.datetime(2020, 1, 1, tzinfo=datetime.UTC), to
    if window.endswith("d") and window[:-1].isdigit():
        return to - datetime.timedelta(days=int(window[:-1])), to
    raise HTTPException(422, f"invalid window {window!r} — expected '<N>d' or 'all'")
