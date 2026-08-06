"""ARCHITECTURE.md §15/§14 — `GET /api/research/notes`, read-only (the
write side is `kairodex research import-notes`, a CLI command per §14's
own "Return path," not an API write)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kairodex.api.deps import get_session
from kairodex.store.models import ResearchNote

router = APIRouter(prefix="/research", tags=["research"])


@router.get("/notes")
async def list_research_notes(
    limit: int = Query(50, le=200), session: AsyncSession = Depends(get_session)
) -> list[dict[str, object]]:
    rows = list(
        await session.scalars(
            select(ResearchNote).order_by(ResearchNote.created_at.desc()).limit(limit)
        )
    )
    return [
        {
            "note_id": n.note_id,
            "created_at": n.created_at,
            "segment": n.segment.value if n.segment else None,
            "bundle_id": n.bundle_id,
            "source": n.source,
            "status": n.status,
            "findings": n.findings,
            "actions": n.actions,
            "applied_strategy_ids": n.applied_strategy_ids,
        }
        for n in rows
    ]
