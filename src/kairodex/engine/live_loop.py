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
from kairodex.data.recorder import watchlist_instruments
from kairodex.engine.orchestrator import run_entry_tick, run_exit_tick
from kairodex.execution.costs import compute_nse_costs, compute_us_costs
from kairodex.execution.simulator import ExecutionPort, ShadowLogger, SimulatedBroker
from kairodex.risk.loader import build_account_state
from kairodex.store.base import get_sessionmaker
from kairodex.store.models import Strategy as StrategyRow
from kairodex.store.models import Trade
from kairodex.strategy.protocol import ReferenceStrategy
from kairodex.strategy.scorer import ConfluenceScorer

logger = logging.getLogger(__name__)

EVAL_INTERVAL = datetime.timedelta(seconds=60)


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

    while True:
        now = clock.now()
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
                except Exception:
                    logger.exception("exit tick failed for trade %d", trade.trade_id)

        await asyncio.sleep(EVAL_INTERVAL.total_seconds())
