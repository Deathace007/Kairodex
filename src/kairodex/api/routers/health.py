from __future__ import annotations

import datetime

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kairodex.api.deps import get_session
from kairodex.status import gap_rate
from kairodex.store.models import FeedHealth

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/feeds")
async def feed_health(session: AsyncSession = Depends(get_session)) -> list[dict[str, object]]:
    """Per-market connection state, last message age, quota, 24h gap rate
    — the same facts `kairodex status` reports as text (ARCHITECTURE.md
    §17), reusing its own `gap_rate` helper rather than a second copy."""
    now = datetime.datetime.now(datetime.UTC)
    since = now - datetime.timedelta(hours=24)
    rows = list(await session.scalars(select(FeedHealth)))
    out = []
    for row in rows:
        last_age_secs = (
            (now - row.last_message_at).total_seconds() if row.last_message_at else None
        )
        out.append(
            {
                "provider": row.provider,
                "connected": row.connected,
                "last_message_age_secs": last_age_secs,
                "subscribed_count": row.subscribed_count,
                "quota_used_pct": row.quota_used_pct,
                "clock_skew_ms": row.clock_skew_ms,
                "gap_rate_24h": await gap_rate(session, row.provider, since),
                "last_error": row.last_error,
                "last_error_at": row.last_error_at.isoformat() if row.last_error_at else None,
            }
        )
    return out
