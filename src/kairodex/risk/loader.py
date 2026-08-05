"""Builds an `AccountState` from the DB for one segment — the one
DB-touching file in `kairodex.risk`, same separation as
`kairodex.features.loader`/`kairodex.strategy.contract_selector`'s
counterparts: every gate is testable against a synthetic `AccountState`,
this file is what makes a real one.
"""

from __future__ import annotations

import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kairodex.core.enums import Market, Segment
from kairodex.risk.types import AccountState
from kairodex.store.models import EquitySnapshot, Instrument, RiskState, Trade, TradingCalendar

_LIVE_RUN_ID = 0  # EquitySnapshot's sentinel for "live paper book" — see its model docstring


async def is_session_open(
    session: AsyncSession, market: Market, now: datetime.datetime
) -> bool:
    """Checks `trading_calendar` first; falls back to an approximate,
    DST-naive hardcoded window when no row exists for today — a real
    vendor holiday/session-hours sync (ARCHITECTURE.md §6) hasn't been
    built yet (same gap `kairodex.risk.gates.session_window_gate`'s own
    docstring names). This fallback exists so live verification during
    actual market hours is possible at all before that sync exists;
    `trading_calendar` data always wins once it's populated.
    ponytail: ignores exchange holidays and DST entirely — ok for "is a
    signal roughly plausible right now" during interactive verification,
    not a substitute for the real calendar.
    """
    exchange = "NSE" if market is Market.NSE else "US"
    today = now.date()
    calendar_row = await session.get(
        TradingCalendar, {"exchange": exchange, "session_date": today}
    )
    if calendar_row is not None:
        no_hours = calendar_row.open_utc is None or calendar_row.close_utc is None
        if calendar_row.is_holiday or no_hours:
            return False
        return calendar_row.open_utc <= now <= calendar_row.close_utc  # type: ignore[operator]

    if market is Market.NSE:  # 9:15-15:30 IST = 3:45-10:00 UTC, no DST
        open_t, close_t = datetime.time(3, 45), datetime.time(10, 0)
    else:  # US: 9:30-16:00 ET, approximated as 13:30-20:00 UTC (EST; off by 1h in EDT)
        open_t, close_t = datetime.time(13, 30), datetime.time(20, 0)
    now_t = now.timetz().replace(tzinfo=None)
    return open_t <= now_t <= close_t


async def build_account_state(
    session: AsyncSession,
    segment: Segment,
    now: datetime.datetime,
    *,
    kill_switch_engaged: bool = False,
) -> AccountState:
    default_capital = Decimal("50000")  # overwritten below once an equity_snapshots row exists

    risk_state = await session.get(RiskState, segment)
    latest_snapshot = await session.scalar(
        select(EquitySnapshot)
        .where(EquitySnapshot.segment == segment, EquitySnapshot.run_id == _LIVE_RUN_ID)
        .order_by(EquitySnapshot.ts.desc())
        .limit(1)
    )
    equity = latest_snapshot.equity if latest_snapshot is not None else default_capital
    hwm = latest_snapshot.high_water_mark if latest_snapshot is not None else default_capital

    open_trades = list(
        await session.scalars(
            select(Trade).where(
                Trade.segment == segment, Trade.run_id.is_(None), Trade.closed_at.is_(None)
            )
        )
    )
    open_underlying_ids = {t.underlying_id for t in open_trades}
    open_underlyings: frozenset[str] = frozenset()
    if open_underlying_ids:
        rows = await session.scalars(
            select(Instrument.symbol).where(Instrument.instrument_id.in_(open_underlying_ids))
        )
        open_underlyings = frozenset(rows)
    total_exposure = sum((t.premium_paid for t in open_trades), start=Decimal(0))

    closed_losses = list(
        await session.scalars(
            select(Trade)
            .where(
                Trade.segment == segment,
                Trade.run_id.is_(None),
                Trade.closed_at.is_not(None),
                Trade.net_pnl < 0,
            )
            .order_by(Trade.closed_at.desc())
        )
    )
    last_loss_ts_by_underlying: dict[str, datetime.datetime] = {}
    if closed_losses:
        underlying_ids = {t.underlying_id for t in closed_losses}
        symbol_rows = await session.execute(
            select(Instrument.instrument_id, Instrument.symbol).where(
                Instrument.instrument_id.in_(underlying_ids)
            )
        )
        symbol_by_id: dict[int, str] = {row[0]: row[1] for row in symbol_rows}
        for t in closed_losses:  # already sorted newest-first; first write per symbol wins
            symbol = symbol_by_id.get(t.underlying_id)
            if symbol is not None and symbol not in last_loss_ts_by_underlying:
                last_loss_ts_by_underlying[symbol] = t.closed_at  # type: ignore[assignment]

    session_open = await is_session_open(session, segment.market, now)

    return AccountState(
        segment=segment,
        equity=equity,
        high_water_mark=hwm,
        daily_pnl=risk_state.daily_pnl if risk_state else Decimal(0),
        weekly_pnl=risk_state.weekly_pnl if risk_state else Decimal(0),
        consecutive_losses=risk_state.consecutive_losses if risk_state else 0,
        breaker_status=risk_state.breaker_status if risk_state else "ARMED",
        breaker_reason=risk_state.breaker_reason if risk_state else None,
        blocked_until=risk_state.blocked_until if risk_state else None,
        kill_switch_engaged=kill_switch_engaged,
        open_positions_count=len(open_trades),
        open_underlyings=open_underlyings,
        total_exposure=total_exposure,
        last_loss_ts_by_underlying=last_loss_ts_by_underlying,
        session_open=session_open,
    )
