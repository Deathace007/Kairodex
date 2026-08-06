"""ARCHITECTURE.md §15 — every `/api/segments/{seg}/...` endpoint. Reads
only; `kairodex.analytics` does the actual metric math, this file is
query-building and response shaping.
"""

from __future__ import annotations

import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from kairodex.analytics import breakdowns, performance
from kairodex.analytics import loader as analytics_loader
from kairodex.analytics.types import PerformanceSummary
from kairodex.api.deps import get_session, parse_iso_date, parse_window
from kairodex.core.enums import Segment
from kairodex.export.bundle import trade_export
from kairodex.store.models import (
    Instrument,
    OptionQuote,
    RiskState,
    Signal,
    Trade,
    TradeEvent,
)

router = APIRouter(prefix="/segments", tags=["segments"])


@router.get("")
async def list_segments() -> list[str]:
    return [s.value for s in Segment]


@router.get("/{segment}/overview")
async def segment_overview(
    segment: Segment, session: AsyncSession = Depends(get_session)
) -> dict[str, object]:
    frm, to = parse_window("7d")
    trades = await analytics_loader.load_trades(session, segment, frm=frm, to=to)
    equity = await analytics_loader.load_equity_curve(session, segment, frm=frm, to=to)
    open_count = await session.scalar(
        select(func.count()).select_from(Trade).where(
            Trade.segment == segment, Trade.run_id.is_(None), Trade.closed_at.is_(None)
        )
    )
    recent_signals = await session.scalar(
        select(func.count()).select_from(Signal).where(
            Signal.segment == segment, Signal.ts >= datetime.datetime.now(datetime.UTC)
            - datetime.timedelta(hours=24),
        )
    )
    risk_state = await session.get(RiskState, segment)
    return {
        "segment": segment.value,
        "open_positions": open_count or 0,
        "signals_24h": recent_signals or 0,
        "performance_7d": performance.summarize(trades),
        "equity": performance.equity_curve_stats(equity),
        "breaker_status": risk_state.breaker_status if risk_state else "ARMED",
    }


@router.get("/{segment}/positions")
async def segment_positions(
    segment: Segment, session: AsyncSession = Depends(get_session)
) -> list[dict[str, object]]:
    """Open positions with the latest quoted Greeks for their own leg —
    `position_marks.greeks` is never populated by the engine (nothing
    computes it at mark time yet), so this reads the same live
    `option_quotes` row the engine's own monitor loop marks against."""
    trades = list(
        await session.scalars(
            select(Trade).where(
                Trade.segment == segment, Trade.run_id.is_(None), Trade.closed_at.is_(None)
            )
        )
    )
    out = []
    for t in trades:
        quote = await session.scalar(
            select(OptionQuote)
            .where(OptionQuote.instrument_id == t.instrument_id)
            .order_by(OptionQuote.ts.desc())
            .limit(1)
        )
        instrument = await session.get(Instrument, t.instrument_id)
        unrealized = (
            (quote.ltp - t.avg_entry) * t.qty_lots * t.lot_size
            if quote is not None and quote.ltp is not None
            else None
        )
        out.append(
            {
                "trade_id": t.trade_id,
                "instrument_symbol": instrument.symbol if instrument else None,
                "opened_at": t.opened_at,
                "qty_lots": t.qty_lots,
                "avg_entry": t.avg_entry,
                "mark": quote.ltp if quote else None,
                "unrealized": unrealized,
                "greeks": {
                    "iv": quote.iv if quote else None,
                    "delta": quote.delta if quote else None,
                    "gamma": quote.gamma if quote else None,
                    "theta": quote.theta if quote else None,
                    "vega": quote.vega if quote else None,
                }
                if quote
                else None,
                "stop_price": (t.risk_params or {}).get("stop_price"),
                "risk_params": t.risk_params,
            }
        )
    return out


@router.get("/{segment}/opportunities")
async def segment_opportunities(
    segment: Segment,
    limit: int = Query(50, le=200),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, object]]:
    """Every signal in the last hour — taken or rejected, with the
    confluence evidence breakdown, newest first. "Opportunities" in the
    spec's sense includes what was declined and why, not just fills."""
    since = datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=1)
    rows = list(
        await session.scalars(
            select(Signal)
            .where(Signal.segment == segment, Signal.ts >= since)
            .order_by(Signal.ts.desc())
            .limit(limit)
        )
    )
    underlying_ids = {r.underlying_id for r in rows}
    symbols: dict[int, str] = {}
    if underlying_ids:
        for inst_id, sym in (
            await session.execute(
                select(Instrument.instrument_id, Instrument.symbol).where(
                    Instrument.instrument_id.in_(underlying_ids)
                )
            )
        ).all():
            symbols[inst_id] = sym
    return [
        {
            "signal_id": r.signal_id,
            "ts": r.ts,
            "underlying_symbol": symbols.get(r.underlying_id),
            "direction": r.direction.value,
            "confidence": r.confidence,
            "decision": r.decision,
            "reject_stage": r.reject_stage,
            "reject_reason": r.reject_reason,
            "evidence": r.evidence,
        }
        for r in rows
    ]


@router.get("/{segment}/trades")
async def segment_trades(
    segment: Segment,
    frm: str | None = Query(None, alias="from"),
    to: str | None = Query(None, alias="to"),
    strategy: int | None = None,
    outcome: str | None = Query(None, description="'win' | 'loss' | 'open'"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, le=200),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    frm_dt = parse_iso_date(frm, "from")
    to_dt = parse_iso_date(to, "to")
    if to_dt is not None:
        to_dt += datetime.timedelta(days=1)
    trades = await analytics_loader.load_trades(session, segment, frm=frm_dt, to=to_dt)
    if strategy is not None:
        trades = [t for t in trades if t.strategy_id == strategy]
    if outcome == "open":
        trades = [t for t in trades if not t.is_closed]
    elif outcome == "win":
        trades = [t for t in trades if t.net_pnl is not None and t.net_pnl > 0]
    elif outcome == "loss":
        trades = [t for t in trades if t.net_pnl is not None and t.net_pnl < 0]
    total = len(trades)
    start = (page - 1) * page_size
    page_rows = trades[start : start + page_size]
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        # trade_export (kairodex.export.bundle), not the raw TradeRecord
        # dataclass — TradeRecord.r_multiple/is_closed/underlying_px_entry
        # are @property, not fields, so FastAPI's dataclass-only
        # jsonable_encoder silently drops them (caught by a P6 subagent
        # review: the trade-history "R" column could never show a value).
        # TradeExport declares r_multiple as a real field, computed once.
        "trades": [trade_export(t) for t in page_rows],
    }


@router.get("/{segment}/trades/{trade_id}")
async def segment_trade_detail(
    segment: Segment, trade_id: int, session: AsyncSession = Depends(get_session)
) -> dict[str, object]:
    """Full event timeline + `chart_ref` for one trade — ARCHITECTURE.md
    §15's drill-down endpoint, backed directly by `trade_events` (the
    source of truth, Principle 2), not a reconstruction from `trades`."""
    trade = await session.get(Trade, trade_id)
    if trade is None or trade.segment != segment:
        raise HTTPException(404, f"no trade {trade_id} in segment {segment.value}")
    events = list(
        await session.scalars(
            select(TradeEvent).where(TradeEvent.trade_id == trade_id).order_by(TradeEvent.seq)
        )
    )
    underlying = aliased(Instrument)
    row = (
        await session.execute(
            select(Instrument, underlying)
            .join(underlying, underlying.instrument_id == trade.underlying_id)
            .where(Instrument.instrument_id == trade.instrument_id)
        )
    ).first()
    instrument, underlying_row = row if row else (None, None)

    # Same formula as analytics.types.TradeRecord.r_multiple — computed
    # inline here since this endpoint builds its "trade" dict directly
    # from the ORM row rather than through analytics.loader.load_trades.
    r_multiple = None
    initial_stop_raw = (trade.risk_params or {}).get("initial_stop_price")
    if trade.avg_exit is not None and initial_stop_raw is not None:
        risk = trade.avg_entry - Decimal(str(initial_stop_raw))
        if risk != 0:
            r_multiple = float((trade.avg_exit - trade.avg_entry) / risk)

    return {
        "trade": {
            "trade_id": trade.trade_id,
            "strategy_id": trade.strategy_id,
            "instrument_symbol": instrument.symbol if instrument else None,
            "underlying_symbol": underlying_row.symbol if underlying_row else None,
            "opened_at": trade.opened_at,
            "closed_at": trade.closed_at,
            "qty_lots": trade.qty_lots,
            "lot_size": trade.lot_size,
            "avg_entry": trade.avg_entry,
            "avg_exit": trade.avg_exit,
            "gross_pnl": trade.gross_pnl,
            "net_pnl": trade.net_pnl,
            "fees": trade.fees,
            "r_multiple": r_multiple,
            "holding_secs": trade.holding_secs,
            "exit_reason": trade.exit_reason,
            "risk_params": trade.risk_params,
            "context_entry": trade.context_entry,
            "context_exit": trade.context_exit,
        },
        "events": [
            {
                "seq": e.seq, "ts": e.ts, "event_type": e.event_type, "payload": e.payload,
            }
            for e in events
        ],
        "chart_ref": trade.chart_ref,
    }


@router.get("/{segment}/signals")
async def segment_signals(
    segment: Segment,
    decision: str | None = Query(None, description="'TAKEN' | 'REJECTED'"),
    limit: int = Query(100, le=500),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, object]]:
    stmt = select(Signal).where(Signal.segment == segment)
    if decision:
        stmt = stmt.where(Signal.decision == decision.upper())
    rows = await session.scalars(stmt.order_by(Signal.ts.desc()).limit(limit))
    return [
        {
            "signal_id": r.signal_id,
            "ts": r.ts,
            "underlying_id": r.underlying_id,
            "direction": r.direction.value,
            "confidence": r.confidence,
            "decision": r.decision,
            "reject_stage": r.reject_stage,
            "reject_reason": r.reject_reason,
            "evidence": r.evidence,
            "forward_outcome": r.forward_outcome,
        }
        for r in rows
    ]


@router.get("/{segment}/performance")
async def segment_performance(
    segment: Segment,
    window: str | None = Query(None, description="'7d' | '30d' | '90d' | 'all'"),
    session: AsyncSession = Depends(get_session),
) -> PerformanceSummary:
    frm, to = parse_window(window)
    trades = await analytics_loader.load_trades(session, segment, frm=frm, to=to)
    return performance.summarize(trades)


@router.get("/{segment}/equity-curve")
async def segment_equity_curve(
    segment: Segment,
    window: str | None = Query(None, description="'7d' | '30d' | '90d' | 'all'"),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, object]]:
    frm, to = parse_window(window)
    points = await analytics_loader.load_equity_curve(session, segment, frm=frm, to=to)
    return [
        {
            "ts": p.ts, "equity": p.equity, "high_water_mark": p.high_water_mark,
            "drawdown": p.drawdown, "exposure": p.exposure,
        }
        for p in points
    ]


_BREAKDOWN_DIMS = ("regime", "weekday", "session", "expiry", "moneyness", "vol_regime")


@router.get("/{segment}/analytics/breakdown")
async def segment_breakdown(
    segment: Segment,
    by: str = Query(..., description=f"one of {_BREAKDOWN_DIMS}"),
    window: str | None = Query(None),
    session: AsyncSession = Depends(get_session),
) -> dict[str, PerformanceSummary]:
    if by not in _BREAKDOWN_DIMS:
        raise HTTPException(422, f"by must be one of {_BREAKDOWN_DIMS}")
    frm, to = parse_window(window)
    trades = await analytics_loader.load_trades(session, segment, frm=frm, to=to)
    return breakdowns.breakdown(trades, by)


@router.get("/{segment}/risk")
async def segment_risk(
    segment: Segment, session: AsyncSession = Depends(get_session)
) -> dict[str, object]:
    from kairodex.config.segments import get_segment_config

    risk_state = await session.get(RiskState, segment)
    config = get_segment_config(segment)
    return {
        "segment": segment.value,
        "config": config.model_dump(),
        "daily_pnl": risk_state.daily_pnl if risk_state else None,
        "weekly_pnl": risk_state.weekly_pnl if risk_state else None,
        "consecutive_losses": risk_state.consecutive_losses if risk_state else 0,
        "breaker_status": risk_state.breaker_status if risk_state else "ARMED",
        "breaker_reason": risk_state.breaker_reason if risk_state else None,
        "blocked_until": risk_state.blocked_until if risk_state else None,
        "risk_multiplier": risk_state.risk_multiplier if risk_state else None,
    }
