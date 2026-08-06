"""`kairodex research import-notes findings.json` — the return half of
ARCHITECTURE.md §14's loop: a bundle went out, got reviewed (by a human
or by Claude Code in a fresh session), and the findings come back in as a
`research_notes` row, linked to whichever strategies they apply to.
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from kairodex.core.enums import Segment
from kairodex.store.models import ResearchNote


class FindingsImport(BaseModel):
    """The expected shape of a `findings.json` a reviewer hands back.
    `findings` is the only required field — a note with nothing found is
    still a note ("reviewed, nothing actionable"), but a note that
    doesn't say *what* was reviewed isn't useful."""

    segment: Segment | None = None
    bundle_id: str | None = None
    source: str = "claude-code"
    findings: list[dict[str, Any]] | dict[str, Any]
    actions: list[dict[str, Any]] | dict[str, Any] | None = None
    applied_strategy_ids: list[int] = Field(default_factory=list)
    status: str = "open"


async def import_notes(session: AsyncSession, path: Path) -> ResearchNote:
    raw = json.loads(path.read_text())
    parsed = FindingsImport.model_validate(raw)
    note = ResearchNote(
        created_at=datetime.datetime.now(datetime.UTC),
        segment=parsed.segment,
        bundle_id=parsed.bundle_id,
        source=parsed.source,
        findings=parsed.findings,
        actions=parsed.actions,
        applied_strategy_ids=parsed.applied_strategy_ids,
        status=parsed.status,
    )
    session.add(note)
    await session.commit()
    return note
