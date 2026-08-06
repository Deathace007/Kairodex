"""The engine process (ARCHITECTURE.md §3: `engine --segment
{nse_stock,nse_index,us_stock,us_index}`, one per segment, restart-policy
always). Shadow mode by default — `ShadowLogger` wrapping
`SimulatedBroker`, zero real capital — matching P3's own exit criterion
("full lifecycle runs in shadow mode for 5 sessions"), not live paper
trading, which is a deliberate later switch, not this module's default.
"""

from __future__ import annotations

import asyncio
import datetime
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kairodex.config.segments import get_segment_config
from kairodex.core.clock import LiveClock
from kairodex.core.enums import Market, Segment, StrategyStatus
from kairodex.core.sessions import is_session_open_now
from kairodex.data.recorder import watchlist_instruments
from kairodex.engine.orchestrator import run_entry_tick, run_exit_tick
from kairodex.execution.costs import compute_nse_costs, compute_us_costs
from kairodex.execution.simulator import ExecutionPort, ShadowLogger, SimulatedBroker
from kairodex.risk.accounting import update_equity_and_risk_state
from kairodex.risk.loader import build_account_state
from kairodex.store.base import get_sessionmaker
from kairodex.store.models import PositionMark, RiskState, Trade
from kairodex.store.models import Strategy as StrategyRow
from kairodex.strategy.protocol import ReferenceStrategy
from kairodex.strategy.scorer import ConfluenceScorer
from kairodex.streaming.bus import publish
from kairodex.streaming.types import StreamMessage

logger = logging.getLogger(__name__)

EVAL_INTERVAL = datetime.timedelta(seconds=60)
# How long to idle between checks while this segment's market is closed.
# Nothing can change in between: no new quotes arrive, so no signal could
# score differently and no exit could fill (the fill model rejects on
# STALE_QUOTE anyway). Same reasoning, and the same shared
# `core.sessions` window, as the recorder's own closed-market idle.
CLOSED_MARKET_INTERVAL = datetime.timedelta(minutes=5)


async def _ensure_strategy_row(
    session: AsyncSession, segment: Segment, name: str, version: int = 1
) -> int:
    row = await session.scalar(
        select(StrategyRow).where(
            StrategyRow.segment == segment, StrategyRow.name == name, StrategyRow.version == version
        )
    )
    if row is not None:
        return row.strategy_id
    row = StrategyRow(segment=segment, name=name, version=version, status=StrategyStatus.SHADOW)
    session.add(row)
    await session.flush()
    await session.commit()
    return row.strategy_id


async def run_segment(segment: Segment, *, shadow: bool = True) -> None:
    sessionmaker = get_sessionmaker()
    clock = LiveClock()
    strategy = ReferenceStrategy()
    scorer = ConfluenceScorer()
    cost_model = compute_nse_costs if segment.market is Market.NSE else compute_us_costs
    broker = SimulatedBroker(cost_model=cost_model)
    execution: ExecutionPort = ShadowLogger(broker) if shadow else broker
    config = get_segment_config(segment)

    async with sessionmaker() as session:
        strategy_row_id = await _ensure_strategy_row(session, segment, strategy.id)
    logger.info(
        "%s: engine starting (shadow=%s, strategy_row_id=%d)",
        segment.value,
        shadow,
        strategy_row_id,
    )

    market_was_open: bool | None = None
    while True:
        now = clock.now()

        # Evaluate nothing at all while this segment's market is closed.
        # `session_window_gate` already *rejects* such entries, but it runs
        # as a risk gate — i.e. after the whole tick has computed features,
        # scored a signal, and written a `signals` row. Live 2026-08-06 that
        # produced 21,791 OUTSIDE_SESSION_WINDOW rows (~49% of every signal
        # ever recorded), which is not merely wasted work: ARCHITECTURE.md
        # §11 keeps rejections as training data, and a rejection that only
        # means "the exchange was shut" teaches nothing while crowding out
        # real ones — including on the dashboard's own opportunities feed,
        # which is where a user noticed NSE names at 23:57 IST.
        #
        # The gate stays in the chain as defence in depth (backtest replay
        # drives a different clock through the same gates, and it is the
        # backstop if this loop check is ever bypassed) — this just stops
        # the engine from doing the work to reach it.
        if not is_session_open_now(segment.market, now):
            if market_was_open is not False:
                logger.info(
                    "%s: market closed — idling until the next session", segment.value
                )
                market_was_open = False
            await asyncio.sleep(CLOSED_MARKET_INTERVAL.total_seconds())
            continue
        if market_was_open is not True:
            logger.info("%s: market open — evaluating", segment.value)
            market_was_open = True

        async with sessionmaker() as session:
            underlyings = await watchlist_instruments(session, segment)
            if not underlyings:
                logger.warning("%s: empty watchlist — run sync-watchlist first", segment.value)
                await asyncio.sleep(EVAL_INTERVAL.total_seconds())
                continue

            account = await build_account_state(session, segment, now)

            for underlying in underlyings:
                try:
                    outcome = await run_entry_tick(
                        session,
                        segment=segment,
                        underlying=underlying,
                        strategy=strategy,
                        scorer=scorer,
                        strategy_row_id=strategy_row_id,
                        config=config,
                        account=account,
                        broker=execution,
                        now=now,
                    )
                    if outcome is not None:
                        logger.info(
                            "%s: signal %d %s (%s/%s)",
                            underlying.symbol,
                            outcome.signal_id,
                            "TAKEN" if outcome.taken else "REJECTED",
                            outcome.reject_stage,
                            outcome.reject_reason,
                        )
                        await publish(
                            StreamMessage(
                                type="signal",
                                segment=segment.value,
                                ts=now,
                                data={
                                    "signal_id": outcome.signal_id,
                                    "underlying_symbol": underlying.symbol,
                                    "taken": outcome.taken,
                                    "reject_stage": outcome.reject_stage,
                                    "reject_reason": outcome.reject_reason,
                                    "trade_id": outcome.trade_id,
                                },
                            )
                        )
                except Exception:
                    logger.exception(
                        "%s: entry tick failed for %s", segment.value, underlying.symbol
                    )

            open_trades = list(
                await session.scalars(
                    select(Trade).where(
                        Trade.segment == segment, Trade.run_id.is_(None), Trade.closed_at.is_(None)
                    )
                )
            )
            for trade in open_trades:
                try:
                    exit_outcome = await run_exit_tick(
                        session, trade=trade, broker=execution, now=now
                    )
                    if exit_outcome.closed:
                        logger.info("trade %d closed: %s", trade.trade_id, exit_outcome.action)
                        await publish(
                            StreamMessage(
                                type="trade_closed",
                                segment=segment.value,
                                ts=now,
                                data={"trade_id": trade.trade_id, "reason": exit_outcome.action},
                            )
                        )
                    else:
                        mark = await session.scalar(
                            select(PositionMark)
                            .where(PositionMark.trade_id == trade.trade_id)
                            .order_by(PositionMark.ts.desc())
                            .limit(1)
                        )
                        await publish(
                            StreamMessage(
                                type="position_update",
                                segment=segment.value,
                                ts=now,
                                data={
                                    "trade_id": trade.trade_id,
                                    "action": exit_outcome.action,
                                    "mark": str(mark.mark) if mark else None,
                                    "unrealized": str(mark.unrealized) if mark else None,
                                },
                            )
                        )
                except Exception:
                    logger.exception("exit tick failed for trade %d", trade.trade_id)

            try:
                await update_equity_and_risk_state(session, segment, now)
                risk_state = await session.get(RiskState, segment)
                if risk_state is not None:
                    await publish(
                        StreamMessage(
                            type="risk_update",
                            segment=segment.value,
                            ts=now,
                            data={
                                "daily_pnl": str(risk_state.daily_pnl),
                                "weekly_pnl": str(risk_state.weekly_pnl),
                                "consecutive_losses": risk_state.consecutive_losses,
                                "breaker_status": risk_state.breaker_status,
                                "breaker_reason": risk_state.breaker_reason,
                                "risk_multiplier": str(risk_state.risk_multiplier),
                            },
                        )
                    )
            except Exception:
                logger.exception("%s: equity/risk-state update failed", segment.value)

        await asyncio.sleep(EVAL_INTERVAL.total_seconds())
