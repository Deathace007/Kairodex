"""ARCHITECTURE.md §15 — `POST /api/exports`, `GET /api/exports/{id}`.
`kairodex.export` is allowed in-process (only `strategy`/`risk`/`engine`/
`backtest` are forbidden to `kairodex.api` by the import-linter contract),
so this calls `build_bundle` directly rather than shelling out — unlike
`routers/backtests.py`, which can't.
"""

from __future__ import annotations

import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from kairodex.api.deps import get_session
from kairodex.core.enums import Segment
from kairodex.export.bundle import build_bundle

router = APIRouter(prefix="/exports", tags=["exports"])

_EXPORTS_DIR = Path("data/exports")


class ExportRequest(BaseModel):
    segment: Segment
    frm: datetime.date
    to: datetime.date  # inclusive, same convention as `kairodex export`'s CLI


def _bundle_id(segment: Segment, frm: datetime.date, to: datetime.date) -> str:
    return f"bundle_{segment.value}_{frm.isoformat()}_{to.isoformat()}"


@router.post("")
async def create_export(
    body: ExportRequest, session: AsyncSession = Depends(get_session)
) -> dict[str, object]:
    frm_dt = datetime.datetime.combine(body.frm, datetime.time.min, tzinfo=datetime.UTC)
    to_dt = datetime.datetime.combine(body.to, datetime.time.min, tzinfo=datetime.UTC) + (
        datetime.timedelta(days=1)
    )
    bundle_id = _bundle_id(body.segment, body.frm, body.to)
    out_dir = _EXPORTS_DIR / bundle_id
    manifest_path = await build_bundle(
        session, segment=body.segment, frm=frm_dt, to=to_dt, out_dir=out_dir
    )
    return {"id": bundle_id, "status": "complete", "manifest_path": str(manifest_path)}


@router.get("/{export_id}")
async def get_export(export_id: str) -> dict[str, object]:
    import json

    # export_id is user input used to build a filesystem path — reject
    # anything that isn't a plain filename component before it ever
    # touches Path(), rather than trusting `bundle_id`'s own format.
    if "/" in export_id or "\\" in export_id or export_id in {".", ".."}:
        raise HTTPException(422, "invalid export id")
    manifest_path = _EXPORTS_DIR / export_id / "manifest.json"
    if not manifest_path.exists():
        raise HTTPException(404, f"no export bundle {export_id!r}")
    manifest = json.loads(manifest_path.read_text())
    return {"id": export_id, "status": "complete", "manifest": manifest}
