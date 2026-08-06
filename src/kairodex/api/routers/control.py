"""ARCHITECTURE.md §15 — `POST /api/segments/{seg}/breaker` (trip/re-arm,
audited) and `POST /api/kill` (global halt, audited). Both are human
control actions over already-existing risk state, not business logic —
see `system_state`/`RiskState`'s own docstrings for why these needed a
sticky "manual" marker (`kairodex.risk.accounting` runs every engine tick
and would otherwise silently undo a manual trip within ~60s).
"""

from __future__ import annotations

import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from kairodex.api.deps import get_session
from kairodex.core.enums import Segment
from kairodex.store.models import AuditLog, RiskState, SystemState

router = APIRouter(tags=["control"])


class BreakerRequest(BaseModel):
    action: str  # "trip" | "rearm"
    actor: str
    reason: str | None = None


@router.post("/segments/{segment}/breaker")
async def segment_breaker(
    segment: Segment, body: BreakerRequest, session: AsyncSession = Depends(get_session)
) -> dict[str, object]:
    if body.action not in {"trip", "rearm"}:
        raise HTTPException(422, "action must be 'trip' or 'rearm'")
    now = datetime.datetime.now(datetime.UTC)
    risk_state = await session.get(RiskState, segment)
    if risk_state is None:
        risk_state = RiskState(segment=segment, as_of=now)
        session.add(risk_state)
    before = {
        "breaker_status": risk_state.breaker_status,
        "breaker_reason": risk_state.breaker_reason,
    }

    if body.action == "trip":
        risk_state.breaker_status = "TRIPPED"
        reason_suffix = f": {body.reason}" if body.reason else ""
        risk_state.breaker_reason = f"MANUAL_HALT{reason_suffix}"
    else:
        risk_state.breaker_status = "ARMED"
        risk_state.breaker_reason = None
    risk_state.as_of = now

    session.add(
        AuditLog(
            ts=now, actor=body.actor, action=f"breaker_{body.action}",
            target=f"segments/{segment.value}", before=before,
            after={
                "breaker_status": risk_state.breaker_status,
                "breaker_reason": risk_state.breaker_reason,
            },
        )
    )
    await session.commit()
    return {"segment": segment.value, "breaker_status": risk_state.breaker_status}


class KillRequest(BaseModel):
    action: str  # "engage" | "release"
    actor: str
    reason: str | None = None


@router.post("/kill")
async def kill_switch(
    body: KillRequest, session: AsyncSession = Depends(get_session)
) -> dict[str, object]:
    """Global halt across all four segments — `risk.loader.
    build_account_state` reads `system_state.kill_engaged` by default on
    every gate-chain evaluation, so this takes effect on the *next* tick
    of every running engine process, no restart needed."""
    if body.action not in {"engage", "release"}:
        raise HTTPException(422, "action must be 'engage' or 'release'")
    now = datetime.datetime.now(datetime.UTC)
    state = await session.get(SystemState, 1)
    if state is None:
        state = SystemState(id=1, updated_at=now)
        session.add(state)
    before = {"kill_engaged": state.kill_engaged, "kill_reason": state.kill_reason}

    state.kill_engaged = body.action == "engage"
    state.kill_reason = body.reason if body.action == "engage" else None
    state.updated_at = now

    session.add(
        AuditLog(
            ts=now, actor=body.actor, action=f"kill_{body.action}", target="system",
            before=before,
            after={"kill_engaged": state.kill_engaged, "kill_reason": state.kill_reason},
        )
    )
    await session.commit()
    return {"kill_engaged": state.kill_engaged, "kill_reason": state.kill_reason}
