"""ARCHITECTURE.md §15 — `GET /api/strategies`, `GET /api/strategies/{id}/
report`, `POST /api/strategies/{id}/promote` (human-gated, audited).
"""

from __future__ import annotations

import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kairodex.api.deps import get_session
from kairodex.core.enums import StrategyStatus, is_valid_strategy_transition
from kairodex.store.models import AuditLog, BacktestRun, Strategy, StrategyPromotion

router = APIRouter(prefix="/strategies", tags=["strategies"])


@router.get("")
async def list_strategies(session: AsyncSession = Depends(get_session)) -> list[dict[str, object]]:
    rows = list(await session.scalars(select(Strategy)))
    return [
        {
            "strategy_id": s.strategy_id,
            "segment": s.segment.value,
            "name": s.name,
            "version": s.version,
            "status": s.status.value,
        }
        for s in rows
    ]


@router.get("/{strategy_id}/report")
async def strategy_report(
    strategy_id: int, session: AsyncSession = Depends(get_session)
) -> dict[str, object]:
    strategy = await session.get(Strategy, strategy_id)
    if strategy is None:
        raise HTTPException(404, f"no strategy {strategy_id}")
    promotions = list(
        await session.scalars(
            select(StrategyPromotion)
            .where(StrategyPromotion.strategy_id == strategy_id)
            .order_by(StrategyPromotion.at.desc())
        )
    )
    runs = list(
        await session.scalars(
            select(BacktestRun)
            .where(BacktestRun.strategy_id == strategy_id)
            .order_by(BacktestRun.created_at.desc())
            .limit(10)
        )
    )
    return {
        "strategy_id": strategy.strategy_id,
        "segment": strategy.segment.value,
        "name": strategy.name,
        "version": strategy.version,
        "status": strategy.status.value,
        "code_sha": strategy.code_sha,
        "params": strategy.params,
        "promotions": [
            {
                "from_status": p.from_status.value if p.from_status else None,
                "to_status": p.to_status.value,
                "at": p.at,
                "approved_by": p.approved_by,
                "rationale": p.rationale,
                "validation_report": p.validation_report,
            }
            for p in promotions
        ],
        "recent_backtest_runs": [
            {
                "run_id": r.run_id, "created_at": r.created_at,
                "from_ts": r.from_ts, "to_ts": r.to_ts, "metrics": r.metrics,
                "deflated_sharpe": r.deflated_sharpe,
            }
            for r in runs
        ],
    }


class PromoteRequest(BaseModel):
    to_status: StrategyStatus
    approved_by: str
    rationale: str | None = None


@router.post("/{strategy_id}/promote")
async def promote_strategy(
    strategy_id: int, body: PromoteRequest, session: AsyncSession = Depends(get_session)
) -> dict[str, object]:
    """Human-gated, audited (ARCHITECTURE.md §15). Validates the
    transition is topologically legal (`is_valid_strategy_transition` —
    `kairodex.core.enums`, shared with `kairodex.backtest.promotion`'s
    own state machine) but does NOT re-run the Track A/B gate evaluation
    itself — that's `kairodex backtest run`'s job, and its own report is
    what `approved_by`/`rationale` here are attesting a human already
    reviewed. This endpoint records the decision, it doesn't make it."""
    strategy = await session.get(Strategy, strategy_id)
    if strategy is None:
        raise HTTPException(404, f"no strategy {strategy_id}")
    if not is_valid_strategy_transition(strategy.status, body.to_status):
        raise HTTPException(
            409,
            f"{strategy.status.value} -> {body.to_status.value} is not a legal transition",
        )
    now = datetime.datetime.now(datetime.UTC)
    before_status = strategy.status
    strategy.status = body.to_status
    session.add(
        StrategyPromotion(
            strategy_id=strategy_id,
            from_status=before_status,
            to_status=body.to_status,
            at=now,
            approved_by=body.approved_by,
            rationale=body.rationale,
        )
    )
    session.add(
        AuditLog(
            ts=now,
            actor=body.approved_by,
            action="promote",
            target=f"strategies/{strategy_id}",
            before={"status": before_status.value},
            after={"status": body.to_status.value},
        )
    )
    await session.commit()
    return {"strategy_id": strategy_id, "status": strategy.status.value}
