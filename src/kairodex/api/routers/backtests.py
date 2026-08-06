"""ARCHITECTURE.md §15 — `POST /api/backtests`, `GET /api/backtests/{id}`.

`kairodex.backtest` is forbidden to `kairodex.api` by the import-linter
contract ("API is glue, not business logic") — this shells out to the
same `kairodex backtest run` CLI command (`kairodex/cli.py`) instead of
running the backtest in-process, then reads the resulting `backtest_runs`
row back through the DB, same as everything else in this router package.

ponytail: runs synchronously (awaits the subprocess) and blocks the
request for the backtest's own duration — acceptable for a single-user
tool where backtests over this codebase's real data finish in seconds
(P4's live run: 7,525 signals in well under a minute). Upgrade to a
background task + job-status polling if a backtest window ever grows
large enough to threaten an HTTP timeout.
"""

from __future__ import annotations

import asyncio
import datetime
import re
import sys

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kairodex.api.deps import get_session
from kairodex.core.enums import Segment
from kairodex.store.models import BacktestRun, Strategy

router = APIRouter(prefix="/backtests", tags=["backtests"])

_REFERENCE_STRATEGY_NAME = "reference_v1"  # kairodex.strategy.protocol.ReferenceStrategy.id
_SUBPROCESS_TIMEOUT_SECS = 300


class BacktestRequest(BaseModel):
    segment: Segment
    frm: datetime.date
    to: datetime.date


@router.post("")
async def create_backtest(
    body: BacktestRequest, session: AsyncSession = Depends(get_session)
) -> dict[str, object]:
    strategy = await session.scalar(
        select(Strategy).where(
            Strategy.segment == body.segment, Strategy.name == _REFERENCE_STRATEGY_NAME
        )
    )
    if strategy is None:
        raise HTTPException(
            409,
            f"no strategies row for {body.segment.value} yet — "
            "run `kairodex engine --segment ...` at least once first.",
        )

    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "kairodex.cli", "backtest", "run",
        "--segment", body.segment.value,
        "--from", body.frm.isoformat(),
        "--to", body.to.isoformat(),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=_SUBPROCESS_TIMEOUT_SECS
        )
    except TimeoutError:
        proc.kill()
        raise HTTPException(504, "backtest run timed out") from None
    if proc.returncode != 0:
        raise HTTPException(
            500, f"backtest run failed (exit {proc.returncode}): {stderr.decode()[-2000:]}"
        )

    # Read the *specific* row this subprocess just wrote off its own
    # stdout ("backtest_runs row {run_id} written" — cli.py's own final
    # line), rather than re-querying "newest row for this segment/
    # strategy." A P6 subagent review caught that the re-query approach
    # could return a DIFFERENT run's row: nothing serializes concurrent
    # POSTs (or a POST racing a manually-launched `kairodex backtest
    # run`) against each other, and `created_at` is stamped at the
    # *start* of a run, not at write time, so a slower run started
    # earlier can finish after — and be shadowed by — a faster one
    # started later.
    match = re.search(r"backtest_runs row (\d+) written", stdout.decode())
    if match is None:
        raise HTTPException(
            500, f"subprocess succeeded but wrote no backtest_runs row: {stdout.decode()[-2000:]}"
        )
    run = await session.get(BacktestRun, int(match.group(1)))
    if run is None:
        raise HTTPException(500, f"backtest_runs row {match.group(1)} not found after write")
    return {"id": run.run_id, "status": "complete", "metrics": run.metrics}


@router.get("/{run_id}")
async def get_backtest(
    run_id: int, session: AsyncSession = Depends(get_session)
) -> dict[str, object]:
    run = await session.get(BacktestRun, run_id)
    if run is None:
        raise HTTPException(404, f"no backtest_runs row {run_id}")
    return {
        "id": run.run_id,
        "segment": run.segment.value,
        "strategy_id": run.strategy_id,
        "from_ts": run.from_ts,
        "to_ts": run.to_ts,
        "created_at": run.created_at,
        "metrics": run.metrics,
        "trial_count": run.trial_count,
        "deflated_sharpe": run.deflated_sharpe,
    }
