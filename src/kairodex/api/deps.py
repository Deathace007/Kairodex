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
    """`?window=7d|30d|90d|all` -> a `[frm, to)` pair ending now. `all`
    returns from a fixed pre-launch anchor (2020-01-01, well before this
    project's own first recorded data), which is fine — every query here
    is already indexed on the relevant timestamp column."""
    to = datetime.datetime.now(datetime.UTC)
    if window is None or window == "30d":
        return to - datetime.timedelta(days=30), to
    if window == "all":
        return datetime.datetime(2020, 1, 1, tzinfo=datetime.UTC), to
    if window.endswith("d") and window[:-1].isdigit():
        return to - datetime.timedelta(days=int(window[:-1])), to
    raise HTTPException(422, f"invalid window {window!r} — expected '<N>d' or 'all'")


def parse_iso_date(raw: str | None, param_name: str) -> datetime.datetime | None:
    """`?from=`/`?to=`/`?at=`-style query params — a plain `YYYY-MM-DD`
    (or full ISO timestamp) string, `None` if the caller omitted it,
    HTTP 422 (not a 500, ASGI-traceback-and-all) on anything else. A P6
    subagent review caught `fromisoformat()` called unguarded at two call
    sites — a malformed date crashed the request with an unhandled
    `ValueError` instead of a clean client error."""
    if raw is None:
        return None
    try:
        return datetime.datetime.fromisoformat(raw).replace(tzinfo=datetime.UTC)
    except ValueError as e:
        raise HTTPException(422, f"invalid {param_name}={raw!r}: {e}") from e
