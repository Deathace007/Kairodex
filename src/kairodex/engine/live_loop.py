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

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from kairodex.config.segments import get_segment_config
from kairodex.core.clock import LiveClock
from kairodex.core.enums import Market, Segment, StrategyStatus
from kairodex.core.sessions import is_session_open_now, local_date_for, session_window_utc
from kairodex.data.recorder import watchlist_instruments
from kairodex.engine.orchestrator import run_entry_tick, run_exit_tick
from kairodex.execution.costs import compute_nse_costs, compute_us_costs
from kairodex.execution.simulator import ExecutionPort, ShadowLogger, SimulatedBroker
from kairodex.risk.accounting import update_equity_and_risk_state
from kairodex.risk.loader import build_account_state
from kairodex.store.base import get_sessionmaker
from kairodex.store.models import PositionMark, RiskState, Trade, UnderlyingBar
from kairodex.store.models import Strategy as StrategyRow
from kairodex.strategy.detectors import flow
from kairodex.strategy.protocol import strategy_for
from kairodex.strategy.scorer import ConfluenceScorer
from kairodex.streaming.bus import publish
from kairodex.streaming.types import StreamMessage

logger = logging.getLogger(__name__)

EVAL_INTERVAL = datetime.timedelta(seconds=60)
# `compute_fill`'s own 2s default assumes a live tick stream. This engine
# does not read one: it reads `option_quotes` rows, which the T1 REST poll
# writes once every 60s per contract. Measured live 2026-08-07 over 1,396
# real engine ticks, only 1.9% had a quote younger than 2s — so 98% of
# every exit attempt was rejected STALE_QUOTE, silently, including real
# stop-loss breaches (see run_exit_tick's EXIT_FAILED comment).
#
# Entries and exits get different allowances on purpose. Skipping an entry
# costs nothing — there is always another setup — so entries stay strict
# and only tolerate a little over one poll cycle. Skipping an *exit* means
# carrying a position past its own stop, which is unbounded risk, so exits
# tolerate far more: a slightly stale exit price is a small, bounded
# pricing error, and refusing to exit is not. Past 5 minutes the feed is
# genuinely broken and inventing a fill would be worse than reporting it.
ENTRY_MAX_QUOTE_AGE_MS = 90_000
EXIT_MAX_QUOTE_AGE_MS = 300_000
# How long to idle between checks while this segment's market is closed.
# Nothing can change in between: no new quotes arrive, so no signal could
# score differently and no exit could fill (the fill model rejects on
# STALE_QUOTE anyway). Same reasoning, and the same shared
# `core.sessions` window, as the recorder's own closed-market idle.
CLOSED_MARKET_INTERVAL = datetime.timedelta(minutes=5)
# How many underlyings the entry sweep may evaluate before pausing to check
# every open position's exits.
#
# Measured 2026-08-14 (PROGRESS.md §20b): exits used to run exactly once per
# cycle, *after* the whole entry sweep. `EVAL_INTERVAL` is 60s but a full
# 22-name sweep takes ~150s, so the real gap between two consecutive exit
# checks was ~3.5 minutes — the `position_marks` cadence measured 161-222s
# on average and 280s at worst across all ten of that day's trades. A stop
# cannot be enforced tighter than it is looked at: trade 84 (BHARTIARTL 2020
# CE) was marked 13.45 at 15:01:48 and 11.70 at 15:06:19, and filled at
# 11.58 — **-29.0% against a -20% stop**, a 45% overshoot on the system's
# primary risk control. Trade 82 filled at -21.4% the same way.
#
# Moving the sweep before the entry loop is NOT the fix and was rejected:
# it changes the phase, not the period, so consecutive exit checks would
# still be one full cycle apart. Interleaving is what shortens the gap. At
# ~7s per underlying this puts an exit check roughly every 35s, a 6x
# improvement, for about 24 extra queries per cycle.
#
# Deliberately NOT a second concurrent task with its own cadence: that
# needs a second DB session writing the same trades/equity rows as this
# one, and the ordering bugs that invites are worse than the latency being
# fixed. One session, one task, deterministic — the exits simply get
# looked at more often.
EXIT_SWEEP_EVERY_N_UNDERLYINGS = 5
# Signals this process must have written before a declared detector's
# absence counts as dead rather than quiet. ~2 nse_stock sweeps' worth, far
# more than a live detector has ever gone without firing; relative_strength
# was absent from 3,655 in a row (2026-08-20..09-15).
DETECTOR_LIVENESS_MIN_SIGNALS = 200
# Minutes into the session before "no underlying bars today" means the
# exchange is shut rather than the first bar not having landed yet.
NO_BARS_GRACE_MINUTES = 10
# A sweep this slow is the frozen-cycle-clock failure mode (commit 75e4731:
# 2-20 sweeps a session for nine sessions) coming back.
SLOW_SWEEP_WARN = datetime.timedelta(minutes=5)


async def dead_detectors(
    session: AsyncSession, segment: Segment, declared: frozenset[str], since: datetime.datetime
) -> frozenset[str]:
    """Declared detectors absent from every signal this process has written
    since `since` — once there are enough signals to say so.

    Scoped to this process's own signals, not a trailing window, so a
    restart after fixing a dead detector isn't halted by the rows that
    recorded it dead. Signals keep being written while halted, so the halt
    lifts by itself the moment the detector fires again."""
    row = (
        await session.execute(
            text(
                "SELECT count(DISTINCT s.signal_id) AS n, "
                "array_remove(array_agg(DISTINCT d->>'detector'), NULL) AS seen "
                "FROM signals s LEFT JOIN LATERAL jsonb_array_elements(s.evidence) d ON true "
                "WHERE s.segment::text = :segment AND s.ts >= :since"
            ),
            {"segment": segment.value, "since": since},
        )
    ).one()
    if row.n < DETECTOR_LIVENESS_MIN_SIGNALS:
        return frozenset()
    return declared - frozenset(row.seen or [])


async def exchange_shut_today(
    session: AsyncSession, segment: Segment, now: datetime.datetime
) -> bool:
    """The session clock says open but no underlying bar exists today.

    There is no holiday calendar (core.sessions). On 2026-09-14 the exchange
    was shut and the engine ran a normal day off the previous session's
    frozen quotes — 885 signals, 3 fills. This needs no calendar: a real
    session produces bars within minutes."""
    open_dt, _ = session_window_utc(segment.market, local_date_for(segment.market, now))
    if now < open_dt + datetime.timedelta(minutes=NO_BARS_GRACE_MINUTES):
        return False
    latest = await session.scalar(
        select(func.max(UnderlyingBar.ts)).where(
            UnderlyingBar.timeframe == "1m", UnderlyingBar.ts >= open_dt, UnderlyingBar.ts <= now
        )
    )
    return latest is None


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


async def _sweep_exits(
    session: AsyncSession,
    *,
    segment: Segment,
    exit_execution: ExecutionPort,
    clock: LiveClock,
) -> None:
    """Evaluate exits for every open position in `segment`, once.

    Extracted from `run_segment`'s cycle so it can be called repeatedly
    *during* the entry sweep as well as before it — see
    `EXIT_SWEEP_EVERY_N_UNDERLYINGS` for why the old once-per-cycle
    placement could not enforce a stop tighter than ~3.5 minutes.

    Cheap enough to call often: one query for the open trades plus one
    quote read per trade, against a `max_concurrent` of 5.
    """
    open_trades = list(
        await session.scalars(
            select(Trade).where(
                Trade.segment == segment, Trade.run_id.is_(None), Trade.closed_at.is_(None)
            )
        )
    )
    for trade in open_trades:
        try:
            # Re-read the clock per trade rather than reusing the
            # cycle's `now`. A full cycle walks the whole watchlist
            # and takes tens of seconds, so by the time exits ran,
            # `now` was stale — which made the quote-age check
            # measure the wrong interval, and occasionally *negative*
            # (a quote written after the cycle started looked like it
            # came from the future, and sailed through the staleness
            # check for that reason alone). That is how trade 6's one
            # successful partial exit filled on 2026-08-07 while
            # genuine stop-losses on the same cycle were rejected.
            now = clock.now()
            exit_outcome = await run_exit_tick(
                session, trade=trade, broker=exit_execution, now=now
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


async def run_segment(segment: Segment, *, shadow: bool = True) -> None:
    sessionmaker = get_sessionmaker()
    clock = LiveClock()
    strategy = strategy_for(segment)
    started_at = clock.now()
    halt_logged: str | None = None
    scorer = ConfluenceScorer()
    cost_model = compute_nse_costs if segment.market is Market.NSE else compute_us_costs
    broker = SimulatedBroker(cost_model=cost_model, max_quote_age_ms=ENTRY_MAX_QUOTE_AGE_MS)
    exit_broker = SimulatedBroker(cost_model=cost_model, max_quote_age_ms=EXIT_MAX_QUOTE_AGE_MS)
    execution: ExecutionPort = ShadowLogger(broker) if shadow else broker
    exit_execution: ExecutionPort = ShadowLogger(exit_broker) if shadow else exit_broker
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

        cycle_started = clock.now()
        async with sessionmaker() as session:
            underlyings = await watchlist_instruments(session, segment)
            if not underlyings:
                logger.warning("%s: empty watchlist — run sync-watchlist first", segment.value)
                await asyncio.sleep(EVAL_INTERVAL.total_seconds())
                continue

            # Rebuilt after every fill inside the loop below, not just here.
            # The gates that meter scarcity — max_concurrent, exposure cap,
            # correlation cluster — all read this snapshot, so evaluating a
            # whole watchlist against one pre-tick copy means the Nth
            # underlying is judged as though the first N-1 fills had not
            # happened. Live 2026-08-07, the first tick after US entries
            # started working opened 6 us_stock positions in a single
            # instant against a max_concurrent of 6 that was already holding
            # 4: ten open positions and $6,697 committed against a $6,000
            # exposure cap, with the gate reporting MAX_CONCURRENT correctly
            # on every *later* tick. §14c saw the same shape on nse_stock
            # ("4 of 5 slots in the opening tick") and read it as a missing
            # conviction floor; the floor was necessary but this is the
            # other half.
            #
            # Re-read rather than decrement in place: the loader owns how
            # exposure and equity are derived, and a second copy of that
            # arithmetic here would be free to drift from it. One extra
            # query per *taken* trade, not per underlying.
            account = await build_account_state(session, segment, now)

            # Health halts: entries are rejected at reject_stage "health"
            # (signals and features still recorded), exits run normally.
            halt_reason: str | None = None
            try:
                if await exchange_shut_today(session, segment, now):
                    halt_reason = "EXCHANGE_SHUT_NO_BARS"
                else:
                    dead = await dead_detectors(
                        session, segment, strategy.detector_names, started_at
                    )
                    if dead:
                        halt_reason = "DETECTOR_DEAD:" + ",".join(sorted(dead))
            except Exception:
                logger.exception("%s: health check failed", segment.value)
            if halt_reason != halt_logged:
                if halt_reason is not None:
                    logger.error("%s: ENTRIES HALTED — %s", segment.value, halt_reason)
                else:
                    logger.warning("%s: entries resumed (was %s)", segment.value, halt_logged)
                halt_logged = halt_reason

            # Risk protection ahead of opportunity search. An open position
            # past its stop is unbounded risk that is already running; a
            # setup not yet entered is not. Spending ~150s hunting entries
            # before looking at either is the wrong order, independent of
            # how often the sweep repeats below.
            await _sweep_exits(
                session, segment=segment, exit_execution=exit_execution, clock=clock
            )

            for position, underlying in enumerate(underlyings, start=1):
                # Re-read the clock per underlying, not once per cycle.
                # The cycle-start `now` above is what the session_window
                # check needs, but feeding it to the gates makes every
                # signal in a sweep claim the sweep's *start* time. With a
                # ~150s sweep that is a rounding error; when the chain read
                # regression (features.loader._CHAIN_QUOTE_LOOKBACK) pushed
                # a cycle to 4-5 hours it became total: `now` stayed frozen
                # at the opening bell, so `session_timing_gate` saw ~0
                # minutes-since-open and rejected the entire session as
                # SESSION_WARMUP (42 of the last 8 days' nse_stock
                # rejections, at wall-clock times as late as 14:07 IST),
                # while the next cycle started past `entry_cutoff_minutes`
                # and rejected as SESSION_CLOSING. The legal 09:35-14:00
                # window was never sampled, so nothing could ever be taken.
                # It also stamped `signals.ts`, which is why the dashboard's
                # "Opportunities (last hour)" card was permanently empty.
                now = clock.now()
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
                        # Without this the FLOW family had never fired ONCE —
                        # `prior_as_of=None` means `prior_chain=[]`, which makes
                        # the `oi_change` feature return None, which makes
                        # `oi_price_flow_detector` return None. Across 20,399
                        # real NSE signals `avg_detectors` was exactly 3.00 and
                        # FLOW appeared in zero of them, so `min_families: 2`
                        # has always been 2-of-3, never 2-of-4. Same shape as
                        # the `index_bars` gap that had `relative_strength`
                        # permanently dead (see run_entry_tick's own comment):
                        # a `build_context` parameter nothing ever supplied.
                        prior_as_of=now - flow.OI_LOOKBACK,
                        halt_reason=halt_reason,
                    )
                    if outcome is not None and outcome.taken:
                        account = await build_account_state(session, segment, now)
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

                # Interleaved, not once at the end — this is what actually
                # shortens the gap between two exit checks from a whole
                # cycle to ~35s. See EXIT_SWEEP_EVERY_N_UNDERLYINGS.
                if position % EXIT_SWEEP_EVERY_N_UNDERLYINGS == 0:
                    await _sweep_exits(
                        session, segment=segment, exit_execution=exit_execution, clock=clock
                    )

            sweep_took = clock.now() - cycle_started
            if sweep_took > SLOW_SWEEP_WARN:
                logger.error(
                    "%s: SLOW SWEEP — %d underlyings took %.0fs (expected ~150s)",
                    segment.value,
                    len(underlyings),
                    sweep_took.total_seconds(),
                )

            # Final sweep: the interleaved calls above only fire on exact
            # multiples, so a watchlist whose length is not a multiple of
            # the stride would otherwise leave its tail unswept until the
            # next cycle's leading sweep.
            await _sweep_exits(
                session, segment=segment, exit_execution=exit_execution, clock=clock
            )

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
