"""ARCHITECTURE.md §15/§16 — the Master dashboard's one endpoint: equity
combined across all four segments (native + converted), allocation,
recent trades, risk summary, system health, and Research Insights.
"""

from __future__ import annotations

import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kairodex.analytics import loader as analytics_loader
from kairodex.analytics import performance
from kairodex.api.deps import convert, get_session
from kairodex.core.enums import Segment
from kairodex.store.models import FeedHealth, ResearchNote, RiskState

router = APIRouter(prefix="/master", tags=["master"])


@router.get("/overview")
async def master_overview(
    ccy: str = Query("native", description="'INR' | 'USD' | 'native'"),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    frm = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=30)
    to = datetime.datetime.now(datetime.UTC)

    per_segment: list[dict[str, object]] = []
    for segment in Segment:
        equity_points = await analytics_loader.load_equity_curve(session, segment, frm=frm, to=to)
        stats = performance.equity_curve_stats(equity_points)
        risk_state = await session.get(RiskState, segment)
        converted = None
        if ccy != "native" and stats.current_equity is not None:
            converted = await convert(session, stats.current_equity, segment.currency, ccy)
        per_segment.append(
            {
                "segment": segment.value,
                "currency": segment.currency,
                "equity": stats.current_equity,
                "equity_converted": converted,
                "high_water_mark": stats.high_water_mark,
                "max_drawdown_pct": stats.max_drawdown_pct,
                "breaker_status": risk_state.breaker_status if risk_state else "ARMED",
            }
        )

    feeds = list(await session.scalars(select(FeedHealth)))
    notes = list(
        await session.scalars(
            select(ResearchNote).order_by(ResearchNote.created_at.desc()).limit(10)
        )
    )
    return {
        "ccy": ccy,
        "segments": per_segment,
        "feed_health": [
            {"provider": f.provider, "connected": f.connected} for f in feeds
        ],
        "research_insights": [
            {
                "note_id": n.note_id,
                "created_at": n.created_at,
                "segment": n.segment.value if n.segment else None,
                "status": n.status,
                "findings": n.findings,
            }
            for n in notes
        ],
    }
