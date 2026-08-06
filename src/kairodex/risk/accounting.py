"""Writes `equity_snapshots`/`risk_state` (ARCHITECTURE.md §5.4/§11/§12) —
the DB-touching counterpart nothing previously called: `risk.loader.
build_account_state` reads these two tables and had defaults for when
they're empty, but nothing ever wrote a row into either, so `equity`/
`high_water_mark` stayed pinned at the hardcoded default and `daily_pnl`/
`weekly_pnl`/`consecutive_losses`/`breaker_status` stayed at zero/ARMED
forever — `daily_loss_gate`, `weekly_loss_gate`, `drawdown_throttle_gate`,
`breaker_gate`, and `sizing.risk_multiplier`'s profit/loss scaling were all
structurally inert as a result. `live_loop.run_segment` calls
`update_equity_and_risk_state` once per tick cycle, after entries/exits,
so the next cycle's `build_account_state` sees fresh numbers.

Recomputed from scratch each call (cash/unrealized/pnl/streak), not
incrementally maintained — the trades/position_marks tables are already
the source of truth (Principle 2), and segment position counts are small
(`max_concurrent` <= 6), so a handful of queries per tick is cheap and
never drifts out of sync with what actually happened.
"""

from __future__ import annotations

import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kairodex.config.segments import get_segment_config
from kairodex.core.enums import Segment
from kairodex.risk.sizing import risk_multiplier
from kairodex.store.models import EquitySnapshot, PositionMark, RiskState, Trade

_LIVE_RUN_ID = 0  # matches risk.loader's sentinel


async def _open_trades(session: AsyncSession, segment: Segment) -> list[Trade]:
    return list(
        await session.scalars(
            select(Trade).where(
                Trade.segment == segment, Trade.run_id.is_(None), Trade.closed_at.is_(None)
            )
        )
    )


async def _closed_trades_desc(session: AsyncSession, segment: Segment) -> list[Trade]:
    return list(
        await session.scalars(
            select(Trade)
            .where(
                Trade.segment == segment,
                Trade.run_id.is_(None),
                Trade.closed_at.is_not(None),
            )
            .order_by(Trade.closed_at.desc())
        )
    )


async def _latest_unrealized(session: AsyncSession, trade_id: int) -> Decimal:
    mark = await session.scalar(
        select(PositionMark)
        .where(PositionMark.trade_id == trade_id)
        .order_by(PositionMark.ts.desc())
        .limit(1)
    )
    return mark.unrealized if mark is not None else Decimal(0)


async def update_equity_and_risk_state(
    session: AsyncSession, segment: Segment, now: datetime.datetime
) -> None:
    config = get_segment_config(segment)
    capital = Decimal(str(config.capital))

    closed = await _closed_trades_desc(session, segment)
    realized = sum((t.net_pnl or Decimal(0) for t in closed), start=Decimal(0))
    cash = capital + realized

    open_trades = await _open_trades(session, segment)
    unrealized = Decimal(0)
    for t in open_trades:
        unrealized += await _latest_unrealized(session, t.trade_id)
    exposure = sum((t.premium_paid for t in open_trades), start=Decimal(0))

    equity = cash + unrealized

    prev_snapshot = await session.scalar(
        select(EquitySnapshot)
        .where(EquitySnapshot.segment == segment, EquitySnapshot.run_id == _LIVE_RUN_ID)
        .order_by(EquitySnapshot.ts.desc())
        .limit(1)
    )
    prev_hwm = prev_snapshot.high_water_mark if prev_snapshot is not None else capital
    hwm = max(prev_hwm, equity)
    drawdown = (hwm - equity) / hwm if hwm > 0 else Decimal(0)
    utilization = exposure / equity if equity > 0 else Decimal(0)

    today = now.date()
    week = now.isocalendar()[:2]  # (iso_year, iso_week) — Mon-Sun boundary
    daily_pnl = sum(
        (
            t.net_pnl or Decimal(0)
            for t in closed
            if t.closed_at is not None and t.closed_at.date() == today
        ),
        start=Decimal(0),
    )
    weekly_pnl = sum(
        (
            t.net_pnl or Decimal(0)
            for t in closed
            if t.closed_at is not None and t.closed_at.isocalendar()[:2] == week
        ),
        start=Decimal(0),
    )

    consecutive_losses = 0
    for t in closed:  # newest-first; stop at the first non-loss
        if (t.net_pnl or Decimal(0)) < 0:
            consecutive_losses += 1
        else:
            break

    # Auto-trip: a hard daily/weekly/drawdown breach parks the breaker
    # until the breaching figure itself resets (daily/weekly_pnl are
    # recomputed fresh from "closed today/this week" each call, so a
    # breaker tripped on a bad day un-trips the next calendar day —
    # deliberately stateless, no separate manual-reset workflow for a
    # paper system).
    #
    # Caught directly this session: a MANUAL trip (`POST /api/segments/
    # {seg}/breaker`, `kairodex.api`) needs to actually stay tripped —
    # without this check, this function runs every engine tick
    # (`live_loop.run_segment`) and would silently recompute
    # `breaker_status` back to `"ARMED"` the very next cycle (~60s later)
    # the moment the auto-trip conditions weren't *also* breached,
    # undoing a human's explicit halt before they could act on it.
    # `breaker_reason` starting `"MANUAL_"` is the sentinel
    # (`api.routers.control`'s own convention) — sticky until a human
    # explicitly re-arms it via the same endpoint, matching SPEC_REVIEW.md
    # B8's "both persisted, both requiring explicit human re-arm."
    #
    # `session.get()` alone can return a *stale* row here: this session
    # was opened at the top of `run_segment`'s tick, several seconds
    # before this call, and `expire_on_commit=False` (`store/base.py`)
    # means an intermediate commit from earlier in the same tick doesn't
    # invalidate the identity-mapped `RiskState` object — so a manual
    # trip landing via a *different* session mid-tick can be invisible
    # here (a P6 subagent review traced this exactly). `refresh()` forces
    # a real read, narrowing the race to "between this query and the
    # `UPDATE` below" instead of "the whole ~6.5s active tick phase."
    existing_risk_state = await session.get(RiskState, segment)
    if existing_risk_state is not None:
        await session.refresh(existing_risk_state, attribute_names=["breaker_reason"])
    existing_reason = existing_risk_state.breaker_reason if existing_risk_state else None
    manually_tripped = existing_reason is not None and existing_reason.startswith("MANUAL_")

    if manually_tripped:
        breaker_status: str = "TRIPPED"
        breaker_reason: str | None = existing_reason
    else:
        breaker_status = "ARMED"
        breaker_reason = None
        if equity > 0 and float(-daily_pnl) / float(equity) >= config.daily_loss_limit_pct:
            breaker_status, breaker_reason = "TRIPPED", "DAILY_LOSS_LIMIT_REACHED"
        elif equity > 0 and float(-weekly_pnl) / float(equity) >= config.weekly_loss_limit_pct:
            breaker_status, breaker_reason = "TRIPPED", "WEEKLY_LOSS_LIMIT_REACHED"
        elif float(drawdown) >= config.max_drawdown_pct:
            breaker_status, breaker_reason = "TRIPPED", "MAX_DRAWDOWN_REACHED"

    mult = risk_multiplier(equity, hwm, consecutive_losses)

    session.add(
        EquitySnapshot(
            segment=segment,
            ts=now,
            run_id=_LIVE_RUN_ID,
            cash=cash,
            unrealized=unrealized,
            realized=realized,
            equity=equity,
            exposure=exposure,
            utilization=utilization,
            high_water_mark=hwm,
            drawdown=drawdown,
        )
    )

    risk_state = existing_risk_state
    if risk_state is None:
        risk_state = RiskState(segment=segment, as_of=now)
        session.add(risk_state)
    risk_state.as_of = now
    risk_state.daily_pnl = daily_pnl
    risk_state.weekly_pnl = weekly_pnl
    risk_state.consecutive_losses = consecutive_losses
    risk_state.breaker_status = breaker_status
    risk_state.breaker_reason = breaker_reason
    risk_state.risk_multiplier = Decimal(str(mult))

    await session.commit()
