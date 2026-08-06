"""The one DB-touching file in `kairodex.analytics` — same split as
`kairodex.features.loader`/`kairodex.risk.loader`. Builds
`TradeRecord`/`EquityPoint` lists from `trades`/`instruments`/
`position_marks`/`equity_snapshots`, leaving every metric a pure function
over plain data.
"""

from __future__ import annotations

import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from kairodex.analytics.types import EquityPoint, TradeRecord
from kairodex.core.enums import Segment
from kairodex.store.models import EquitySnapshot, Instrument, PositionMark, Trade

_LIVE_RUN_ID = 0  # EquitySnapshot's own "live paper book" sentinel (risk.loader's same constant)


async def _load_mfe_mae(
    session: AsyncSession, trade_ids: list[int]
) -> dict[int, tuple[Decimal | None, Decimal | None]]:
    if not trade_ids:
        return {}
    rows = (
        await session.execute(
            select(
                PositionMark.trade_id,
                func.max(PositionMark.unrealized),
                func.min(PositionMark.unrealized),
            )
            .where(PositionMark.trade_id.in_(trade_ids))
            .group_by(PositionMark.trade_id)
        )
    ).all()
    return {trade_id: (mx, mn) for trade_id, mx, mn in rows}


async def load_trades(
    session: AsyncSession,
    segment: Segment,
    *,
    frm: datetime.datetime | None = None,
    to: datetime.datetime | None = None,
    run_id: int | None = None,
) -> list[TradeRecord]:
    """`run_id=None` matches `paper_trades`' own filter (`run_id IS NULL`
    = the live/shadow book, ARCHITECTURE.md §5.4) — pass a real backtest
    `run_id` to analyze a backtest run's trades through the same pipeline
    instead."""
    underlying = aliased(Instrument)
    stmt = (
        select(Trade, Instrument, underlying)
        .join(Instrument, Instrument.instrument_id == Trade.instrument_id)
        .join(underlying, underlying.instrument_id == Trade.underlying_id)
        .where(Trade.segment == segment)
        .order_by(Trade.opened_at)
    )
    stmt = stmt.where(Trade.run_id.is_(None)) if run_id is None else stmt.where(
        Trade.run_id == run_id
    )
    if frm is not None:
        stmt = stmt.where(Trade.opened_at >= frm)
    if to is not None:
        stmt = stmt.where(Trade.opened_at < to)

    rows = (await session.execute(stmt)).all()
    mfe_mae = await _load_mfe_mae(session, [t.trade_id for t, _, _ in rows])

    out: list[TradeRecord] = []
    for trade, instrument, under in rows:
        risk_params = trade.risk_params or {}
        stop_raw = risk_params.get("initial_stop_price")
        initial_stop = Decimal(str(stop_raw)) if stop_raw is not None else None
        mfe, mae = mfe_mae.get(trade.trade_id, (None, None))
        out.append(
            TradeRecord(
                trade_id=trade.trade_id,
                segment=trade.segment,
                strategy_id=trade.strategy_id,
                underlying_symbol=under.symbol,
                instrument_symbol=instrument.symbol,
                option_type=instrument.option_type,
                strike=instrument.strike,
                expiry=instrument.expiry,
                opened_at=trade.opened_at,
                closed_at=trade.closed_at,
                avg_entry=trade.avg_entry,
                avg_exit=trade.avg_exit,
                initial_stop_price=initial_stop,
                gross_pnl=trade.gross_pnl,
                net_pnl=trade.net_pnl,
                fees=trade.fees,
                holding_secs=trade.holding_secs,
                exit_reason=trade.exit_reason,
                context_entry=trade.context_entry,
                mfe=mfe,
                mae=mae,
            )
        )
    return out


async def load_equity_curve(
    session: AsyncSession,
    segment: Segment,
    *,
    frm: datetime.datetime | None = None,
    to: datetime.datetime | None = None,
    run_id: int = _LIVE_RUN_ID,
) -> list[EquityPoint]:
    stmt = (
        select(EquitySnapshot)
        .where(EquitySnapshot.segment == segment, EquitySnapshot.run_id == run_id)
        .order_by(EquitySnapshot.ts)
    )
    if frm is not None:
        stmt = stmt.where(EquitySnapshot.ts >= frm)
    if to is not None:
        stmt = stmt.where(EquitySnapshot.ts < to)
    rows = (await session.scalars(stmt)).all()
    return [
        EquityPoint(
            ts=r.ts,
            equity=r.equity,
            high_water_mark=r.high_water_mark,
            drawdown=r.drawdown,
            exposure=r.exposure,
        )
        for r in rows
    ]


async def load_rejected_signals(
    session: AsyncSession,
    segment: Segment,
    *,
    frm: datetime.datetime | None = None,
    to: datetime.datetime | None = None,
) -> list[dict[str, object]]:
    """Every declined opportunity in the window — §14's
    `signals_rejected.jsonl`. Kept as plain dicts (not a dataclass) since
    nothing does math over these, only serializes them."""
    from kairodex.store.models import Signal

    stmt = select(Signal).where(Signal.segment == segment, Signal.decision == "REJECTED")
    if frm is not None:
        stmt = stmt.where(Signal.ts >= frm)
    if to is not None:
        stmt = stmt.where(Signal.ts < to)
    rows = (await session.scalars(stmt.order_by(Signal.ts))).all()
    return [
        {
            "signal_id": r.signal_id,
            "ts": r.ts,
            "underlying_id": r.underlying_id,
            "direction": r.direction.value,
            "confidence": float(r.confidence),
            "reject_stage": r.reject_stage,
            "reject_reason": r.reject_reason,
            "evidence": r.evidence,
        }
        for r in rows
    ]
