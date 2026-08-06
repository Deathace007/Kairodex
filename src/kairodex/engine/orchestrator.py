"""The evaluation loop (ARCHITECTURE.md §2's engine box): Clock -> Features
-> Detectors -> Confluence Scorer -> Contract Selector -> Risk Gate Chain
-> Sizer -> Execution -> Event Log.

Contract selection runs *before* the risk gate chain here, not after, as
the component diagram's box order might suggest at a glance: the
liquidity and capital-available gates need a specific candidate's own
data (its quoted liquidity, its premium) to evaluate at all, so a
concrete contract has to exist first. The diagram is schematic, not a
literal data-flow constraint — nothing in ARCHITECTURE.md's prose
forbids this ordering, and no gate before contract selection depends on
contract-specific data (kill switch, breaker, session, event blackout,
daily/weekly loss, drawdown, max concurrent are all account/segment-level
checks that don't need to know *which* strike yet).

This module is pure glue — every real decision (scoring, gating, sizing,
fill, cost) already lives in an already-tested module; the risk here is
wiring bugs (wrong field, wrong FK), which is exactly what live
verification catches, not what a hand-computed unit test would.
"""

from __future__ import annotations

import dataclasses
import datetime
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kairodex.config.segments import SegmentRiskConfig
from kairodex.core.enums import Segment, Side
from kairodex.data.types import Tick
from kairodex.engine import event_log
from kairodex.engine.monitor import Position, evaluate_exits
from kairodex.execution.simulator import ExecutionPort, ExecutionResult
from kairodex.execution.types import OrderRequest, QuoteSnapshot
from kairodex.features import loader as feature_loader
from kairodex.features import registry as feature_registry
from kairodex.risk.gates import run_gate_chain
from kairodex.risk.sizing import size_position
from kairodex.risk.types import AccountState, TradeProposal
from kairodex.store.models import Fill, Instrument, OptionQuote, Order, PositionMark, Signal, Trade
from kairodex.strategy.contract_selector import ContractCandidate, select_contract
from kairodex.strategy.protocol import Strategy
from kairodex.strategy.scorer import ConfluenceScorer
from kairodex.strategy.types import MarketContext

_DEFAULT_STOP_LOSS_PCT = 0.30  # ponytail: first-pass — a 30%-of-premium
# stop is a common simple options-buying convention; recalibrate once
# backtesting can measure it against real outcomes, per position sizing.
_DEFAULT_MAX_QUOTE_AGE_MS = 2000
_DEFAULT_PROFIT_TARGET_PCT = 1.0  # ponytail: exit at 2x entry premium
# (100% gain) — first-pass, same status as the stop-loss/target-delta
# constants above: a common options-buying convention, not backtested yet.
_DEFAULT_R_MULTIPLE_TARGETS = (1.0, 2.0)  # ponytail: partial exits at 1R/2R
_DEFAULT_MAX_HOLDING_SECS = 3 * 24 * 3600  # ponytail: 3-day theta guard


@dataclass(frozen=True, slots=True)
class TickOutcome:
    signal_id: int
    taken: bool
    reject_stage: str | None
    reject_reason: str | None
    trade_id: int | None = None


def _candidates_from_chain(quotes: list[Tick], expiry: datetime.date) -> list[ContractCandidate]:
    """`quotes` is a list of `kairodex.data.types.Tick` from one
    `ChainSnapshot`'s legs — `expiry` is that snapshot's own expiry
    (every leg in it shares one), passed in rather than guessed, since
    `Tick` itself carries no `expiry` field (see `FeatureContext`'s
    docstring in `kairodex.features.types` for why)."""
    out = []
    for q in quotes:
        if q.strike is None or q.option_type is None or q.bid is None or q.ask is None:
            continue
        out.append(
            ContractCandidate(
                instrument_id=0,  # resolved separately, by instrument_key -> DB lookup
                strike=q.strike,
                option_type=q.option_type,
                expiry=expiry,
                bid=q.bid,
                ask=q.ask,
                delta=q.delta,
                bid_sz=q.bid_sz,
                ask_sz=q.ask_sz,
                oi=q.oi,
                quote_ts=q.ts,
            )
        )
    return out


async def run_entry_tick(
    session: AsyncSession,
    *,
    segment: Segment,
    underlying: Instrument,
    strategy: Strategy,
    scorer: ConfluenceScorer,
    strategy_row_id: int,
    config: SegmentRiskConfig,
    account: AccountState,
    broker: ExecutionPort,
    now: datetime.datetime,
    prior_as_of: datetime.datetime | None = None,
) -> TickOutcome | None:
    """One evaluation pass for one underlying. Returns `None` only if no
    signal was generated at all (nothing to log) — a signal that *was*
    generated but rejected at any stage still gets a `signals` row and a
    `TickOutcome`, per ARCHITECTURE.md §11: "every denial is written to
    signals as a rejected signal. The rejections are training data.\""""
    feature_ctx = await feature_loader.build_context(
        session, segment=segment, underlying=underlying, as_of=now, prior_as_of=prior_as_of
    )
    # build_context deliberately leaves index_bars for the caller (its own
    # docstring) — nothing was ever supplying it, which made
    # relative_strength_detector permanently dead (see load_index_bars's
    # docstring). Missing benchmark data degrades to [] (unchanged), not
    # an error.
    index_bars = await feature_loader.load_index_bars(session, segment, now)
    if index_bars:
        feature_ctx = dataclasses.replace(feature_ctx, index_bars=index_bars)
    values, _quality = feature_registry.compute_all(feature_ctx)
    market_ctx = MarketContext(feature_ctx=feature_ctx, features=values)

    evidence = strategy.evaluate(market_ctx)
    result = scorer.score(evidence)
    if result.direction is None:
        return None  # no signal at all — nothing to persist

    signal = Signal(
        ts=now,
        segment=segment,
        strategy_id=strategy_row_id,
        underlying_id=underlying.instrument_id,
        direction=result.direction,
        confidence=Decimal(str(result.confidence)),
        evidence=[
            {"detector": e.detector, "family": e.family.value, "score": e.score, "weight": e.weight}
            for e in evidence
        ],
        decision="REJECTED",  # overwritten below if every stage clears
    )
    session.add(signal)
    await session.flush()

    if feature_ctx.spot is None or not feature_ctx.chain:
        signal.reject_stage, signal.reject_reason = "contract_selection", "NO_CHAIN_DATA"
        await session.commit()
        return TickOutcome(signal.signal_id, False, signal.reject_stage, signal.reject_reason)

    front = min(feature_ctx.chain, key=lambda s: s.expiry)
    candidates = _candidates_from_chain(front.quotes, front.expiry)

    lot_size = 25 if segment.market.value == "nse" else 100  # ponytail: instrument_specs (P0
    # table, SCD-2 lot size) isn't wired into this path yet — a fixed per-market default
    # stands in; real per-underlying lot sizes vary (see docs/PROGRESS.md's next-steps).
    # Resolved *before* select_contract, not after: the affordability filter inside it
    # depends on the real lot_size (premium * lot_size <= max_premium_pct * equity) —
    # using the wrong lot_size there would silently pass contracts that are actually
    # unaffordable once sized for real.

    selection = select_contract(
        candidates,
        result.direction,
        spot=Decimal(str(feature_ctx.spot)),
        equity=account.equity,
        max_premium_pct=config.max_premium_pct,
        lot_size=lot_size,
        as_of=now.date(),
    )
    if selection.selected is None or selection.mid_price is None:
        signal.reject_stage, signal.reject_reason = "contract_selection", selection.reason
        await session.commit()
        return TickOutcome(signal.signal_id, False, signal.reject_stage, signal.reject_reason)

    candidate = selection.selected
    instrument_id = await _resolve_leg_instrument_id(session, underlying, candidate)
    if instrument_id is None:
        signal.reject_stage = "contract_selection"
        signal.reject_reason = "LEG_INSTRUMENT_NOT_FOUND"
        await session.commit()
        return TickOutcome(signal.signal_id, False, signal.reject_stage, signal.reject_reason)

    stop_distance = selection.mid_price * Decimal(str(_DEFAULT_STOP_LOSS_PCT))
    proposal = TradeProposal(
        segment=segment,
        underlying_symbol=underlying.symbol,
        direction=result.direction,
        confidence=result.confidence,
        ts=now,
        premium_per_lot=selection.mid_price,
        lot_size=lot_size,
        stop_distance=stop_distance,
        liquidity_score=values.get("liquidity_score"),
        # NOT front.complete — caught live: ChainSnapshot.complete needs
        # expected_count, which kairodex.features.loader.load_chain never
        # sets (that field is P1's atomic-single-REST-fetch concept;
        # load_chain instead reconstructs a snapshot leg-by-leg from each
        # instrument's own latest quote, where "complete" isn't a
        # meaningful idea the same way — front.complete is unconditionally
        # False for every chain this pipeline ever builds, which would
        # have made the liquidity gate reject every signal, forever.
        # Genuine incompleteness already surfaces as select_contract
        # simply not finding a valid candidate.
        chain_complete=True,
    )

    chain_result = run_gate_chain(proposal, account, config, now=now)
    if not chain_result.allowed:
        signal.reject_stage = chain_result.reject_stage
        signal.reject_reason = chain_result.reject_reason
        await session.commit()
        return TickOutcome(signal.signal_id, False, signal.reject_stage, signal.reject_reason)

    sizing = size_position(
        equity=account.equity,
        high_water_mark=account.high_water_mark,
        consecutive_losses=account.consecutive_losses,
        config=config,
        stop_distance=stop_distance,
        lot_size=lot_size,
        premium_per_lot=selection.mid_price,
        total_exposure=account.total_exposure,
    )
    if sizing.rejected:
        signal.reject_stage, signal.reject_reason = "sizing", sizing.reject_reason
        await session.commit()
        return TickOutcome(signal.signal_id, False, signal.reject_stage, signal.reject_reason)

    quote = QuoteSnapshot(
        bid=candidate.bid,
        ask=candidate.ask,
        bid_sz=candidate.bid_sz or 0,
        ask_sz=candidate.ask_sz or 0,
        quote_ts=candidate.quote_ts or now,
        oi=candidate.oi,
        chain_complete=proposal.chain_complete,
    )
    order_request = OrderRequest(
        trade_id=0, instrument_id=instrument_id, side=Side.BUY, qty=sizing.lots
    )
    execution = await broker.execute(order_request, quote, now, attempt=1)

    if execution.rejected or execution.filled_qty <= 0:
        signal.reject_stage, signal.reject_reason = "execution", execution.reject_reason
        await session.commit()
        return TickOutcome(signal.signal_id, False, signal.reject_stage, signal.reject_reason)

    trade_id = await _record_fill(
        session,
        segment=segment,
        strategy_row_id=strategy_row_id,
        signal=signal,
        underlying=underlying,
        instrument_id=instrument_id,
        lot_size=lot_size,
        stop_distance=stop_distance,
        execution=execution,
        now=now,
        spot=feature_ctx.spot,
        values=values,
    )
    signal.decision = "TAKEN"
    await session.commit()
    return TickOutcome(signal.signal_id, True, None, None, trade_id=trade_id)


async def _resolve_leg_instrument_id(
    session: AsyncSession, underlying: Instrument, candidate: ContractCandidate
) -> int | None:
    row = await session.scalar(
        select(Instrument).where(
            Instrument.exchange == underlying.exchange,
            Instrument.underlying_symbol == underlying.symbol,
            Instrument.strike == candidate.strike,
            Instrument.option_type == candidate.option_type,
            Instrument.expiry == candidate.expiry,
        )
    )
    return row.instrument_id if row is not None else None


async def _record_fill(
    session: AsyncSession,
    *,
    segment: Segment,
    strategy_row_id: int,
    signal: Signal,
    underlying: Instrument,
    instrument_id: int,
    lot_size: int,
    stop_distance: Decimal,
    execution: ExecutionResult,
    now: datetime.datetime,
    spot: float | None,
    values: dict[str, float],
) -> int:
    assert execution.price is not None
    premium_paid = execution.price * execution.filled_qty * lot_size
    profit_target = execution.price * Decimal(str(1 + _DEFAULT_PROFIT_TARGET_PCT))
    trade = Trade(
        segment=segment,
        strategy_id=strategy_row_id,
        signal_id=signal.signal_id,
        instrument_id=instrument_id,
        underlying_id=underlying.instrument_id,
        opened_at=now,
        qty_lots=execution.filled_qty,
        lot_size=lot_size,
        avg_entry=execution.price,
        premium_paid=premium_paid,
        fees=execution.costs.total if execution.costs is not None else Decimal(0),
        # regime/profile-state/liquidity snapshot at entry (§5.4's own
        # docstring for this column) — everything here was already
        # computed by run_entry_tick's feature pass, so this costs no
        # extra query. Powers P5's breakdown-by-regime/vol_regime/
        # moneyness analytics; previously always NULL (nothing wrote it),
        # which would have made those breakdowns structurally empty.
        context_entry={
            "underlying_px": spot,
            "vol_regime": values.get("volatility_regime"),
            "trend_state_strength": values.get("trend_state_strength"),
            "liquidity_score": values.get("liquidity_score"),
        },
        risk_params={
            "stop_price": str(execution.price - stop_distance),
            "initial_stop_price": str(execution.price - stop_distance),
            # Exit targets (monitor.py's Position fields) — set once at
            # entry and carried in this JSON blob since Trade has no
            # dedicated columns for them. run_exit_tick reads these back;
            # previously nothing wrote them, so profit-target, R-multiple
            # partial, and time-based exits were unreachable in the live
            # engine even though monitor.py fully implements and tests them.
            "profit_target": str(profit_target),
            "r_multiple_targets": list(_DEFAULT_R_MULTIPLE_TARGETS),
            "max_holding_secs": _DEFAULT_MAX_HOLDING_SECS,
        },
    )
    session.add(trade)
    await session.flush()

    order = Order(
        trade_id=trade.trade_id,
        ts=now,
        instrument_id=instrument_id,
        side=Side.BUY,
        qty=execution.filled_qty,
        order_type="MARKET",
        status="FILLED",
    )
    session.add(order)
    await session.flush()

    # spread_bps/slippage_bps: execution already computes these (fills.py
    # -> ExecutionResult) — forwarded here so Fill rows aren't permanently
    # NULL on those columns (P4's Track B "slippage realism" gate reads
    # them, and the export bundle will too).
    fill = Fill(
        order_id=order.order_id,
        ts=now,
        qty=execution.filled_qty,
        price=execution.price,
        spread_bps=(
            Decimal(str(execution.spread_bps)) if execution.spread_bps is not None else None
        ),
        slippage_bps=(
            Decimal(str(execution.slippage_bps)) if execution.slippage_bps is not None else None
        ),
    )
    session.add(fill)

    await event_log.append_event(
        session,
        trade_id=trade.trade_id,
        event_type="FILLED",
        payload={"qty": execution.filled_qty, "price": str(execution.price)},
        ts=now,
    )
    return trade.trade_id


# --- Exit side: monitor + close open positions ------------------------------


@dataclass(frozen=True, slots=True)
class ExitOutcome:
    trade_id: int
    action: str  # "STOP_RATCHET" | "NO_ACTION" | an ExitDecision.reason
    closed: bool
    qty_lots: int = 0


async def run_exit_tick(
    session: AsyncSession,
    *,
    trade: Trade,
    broker: ExecutionPort,
    now: datetime.datetime,
    blackout_active: bool = False,
) -> ExitOutcome:
    """One monitoring pass for one already-open `trade` row — the latest
    quoted mark, run through `kairodex.engine.monitor.evaluate_exits`,
    acted on if it fires. Always writes a `position_marks` row regardless
    of outcome, which is what makes MFE/MAE exact (ARCHITECTURE.md §12)."""
    latest_quote = await _latest_quote(session, trade.instrument_id)
    if latest_quote is None:
        return ExitOutcome(trade.trade_id, "NO_QUOTE", closed=False)
    mark = latest_quote.ltp
    assert mark is not None  # _latest_quote filters on ltp.is_not(None)

    risk_params = trade.risk_params or {}
    stop_price = Decimal(str(risk_params.get("stop_price", mark)))
    initial_stop_price = Decimal(str(risk_params.get("initial_stop_price", mark)))
    hwm_price = max(Decimal(str(risk_params.get("hwm_price", mark))), mark)
    profit_target_raw = risk_params.get("profit_target")
    profit_target = Decimal(str(profit_target_raw)) if profit_target_raw is not None else None
    r_multiple_targets_raw = risk_params.get("r_multiple_targets")
    r_multiple_targets: tuple[float, ...] = (
        tuple(float(x) for x in r_multiple_targets_raw)
        if isinstance(r_multiple_targets_raw, list)
        else _DEFAULT_R_MULTIPLE_TARGETS
    )
    max_holding_secs_raw = risk_params.get("max_holding_secs", _DEFAULT_MAX_HOLDING_SECS)
    max_holding_secs: int | None = (
        int(max_holding_secs_raw) if isinstance(max_holding_secs_raw, int | float) else None
    )
    partial_exits_taken_raw = risk_params.get("partial_exits_taken", [])
    partial_exits_taken = (
        frozenset(partial_exits_taken_raw)
        if isinstance(partial_exits_taken_raw, list)
        else frozenset()
    )

    position = Position(
        trade_id=trade.trade_id,
        segment=trade.segment,
        instrument_id=trade.instrument_id,
        underlying_symbol="",  # not needed by any exit check
        side=Side.BUY,
        qty_lots=trade.qty_lots,
        lot_size=trade.lot_size,
        avg_entry=trade.avg_entry,
        opened_at=trade.opened_at,
        stop_price=stop_price,
        initial_stop_price=initial_stop_price,
        current_mark=mark,
        high_water_mark_price=hwm_price,
        profit_target=profit_target,
        r_multiple_targets=r_multiple_targets,
        partial_exits_taken=partial_exits_taken,
        max_holding_secs=max_holding_secs,
    )

    unrealized = (mark - trade.avg_entry) * trade.qty_lots * trade.lot_size
    session.add(PositionMark(trade_id=trade.trade_id, ts=now, mark=mark, unrealized=unrealized))

    decision = evaluate_exits(position, now, blackout_active=blackout_active)
    if decision is None:
        trade.risk_params = {**risk_params, "hwm_price": str(hwm_price)}
        await session.commit()
        return ExitOutcome(trade.trade_id, "NO_ACTION", closed=False)

    if decision.qty_lots == 0:  # STOP_RATCHET — update the stop, no exit
        new_stop = decision.new_stop_price or stop_price
        trade.risk_params = {
            **risk_params,
            "stop_price": str(new_stop),
            "hwm_price": str(hwm_price),
        }
        await event_log.append_event(
            session,
            trade_id=trade.trade_id,
            event_type="STOP_MOVED",
            payload={"new_stop_price": str(new_stop)},
            ts=now,
        )
        await session.commit()
        return ExitOutcome(trade.trade_id, "STOP_RATCHET", closed=False)

    # Real bid/ask/bid_sz/ask_sz/ts from the same quote row `mark` came
    # from — NOT a synthetic zero-spread quote sized off our own
    # `trade.qty_lots`. That self-referential sizing fed straight into
    # compute_fill's `partial_fill_alpha` cap (max fillable = 25% of
    # `bid_sz`/`ask_sz`), so every exit tick could fill at most 25% of
    # *whatever remained* — the position could shrink forever without
    # ever reaching zero once floor(0.25 * qty_lots) hit 0, permanently
    # stuck open past its own stop-loss. `quote_ts=now` also silently
    # disabled the STALE_QUOTE check on every exit; using the real
    # timestamp restores it.
    quote = QuoteSnapshot(
        bid=latest_quote.bid or mark,
        ask=latest_quote.ask or mark,
        bid_sz=latest_quote.bid_sz or 0,
        ask_sz=latest_quote.ask_sz or 0,
        quote_ts=latest_quote.ts,
        oi=latest_quote.oi,
    )
    order_request = OrderRequest(
        trade_id=trade.trade_id,
        instrument_id=trade.instrument_id,
        side=Side.SELL,
        qty=decision.qty_lots,
    )
    execution = await broker.execute(order_request, quote, now, attempt=1)
    if execution.rejected or execution.filled_qty <= 0:
        await session.commit()  # keep the position_mark write even if the exit couldn't fill
        return ExitOutcome(trade.trade_id, f"{decision.reason}_FILL_FAILED", closed=False)
    assert execution.price is not None  # guaranteed once not rejected and filled_qty > 0

    order = Order(
        trade_id=trade.trade_id, ts=now, instrument_id=trade.instrument_id, side=Side.SELL,
        qty=execution.filled_qty, order_type="MARKET", status="FILLED",
    )
    session.add(order)
    await session.flush()
    fill = Fill(
        order_id=order.order_id,
        ts=now,
        qty=execution.filled_qty,
        price=execution.price,
        spread_bps=(
            Decimal(str(execution.spread_bps)) if execution.spread_bps is not None else None
        ),
        slippage_bps=(
            Decimal(str(execution.slippage_bps)) if execution.slippage_bps is not None else None
        ),
    )
    session.add(fill)

    qty_before_exit = trade.qty_lots  # captured before any mutation below
    remaining_qty = qty_before_exit - execution.filled_qty
    fully_closed = remaining_qty <= 0
    exit_value = execution.price * execution.filled_qty * trade.lot_size
    exit_fees = execution.costs.total if execution.costs is not None else Decimal(0)

    # Realize *this leg's* P&L against its proportional share of the entry
    # cost basis — running totals, so a sequence of partial exits sums to
    # the same total P&L a single full exit would. (Previously gross_pnl/
    # net_pnl were only ever set on the final leg, computed against the
    # *entire original* premium_paid — every earlier partial leg's
    # proceeds were silently dropped, and a position that partially
    # profited before stopping out could show a large phantom loss.)
    #
    # `remaining_entry_fees` (risk_params, not `trade.fees`) tracks the
    # not-yet-allocated share of entry fees, shrinking by each leg's own
    # share the same way `premium_paid` does — the P5 subagent review
    # caught that this used to live in `trade.fees` itself, which made
    # `trade.fees` read as "remaining," not "total": a fully-closed trade
    # always ended up with `fees == 0` regardless of what was actually
    # paid, silently zeroing P5's `total_fees` for every closed trade.
    # `trade.fees` is now a pure running total (entry fee, seeded at
    # `_record_fill`, plus every exit's own fee) that only ever grows.
    remaining_entry_fees_raw = risk_params.get("remaining_entry_fees", str(trade.fees or 0))
    remaining_entry_fees = Decimal(str(remaining_entry_fees_raw))
    entry_fees_this_leg = remaining_entry_fees * execution.filled_qty / qty_before_exit
    cost_basis_this_leg = trade.avg_entry * execution.filled_qty * trade.lot_size
    leg_gross_pnl = exit_value - cost_basis_this_leg
    leg_net_pnl = leg_gross_pnl - entry_fees_this_leg - exit_fees
    trade.gross_pnl = (trade.gross_pnl or Decimal(0)) + leg_gross_pnl
    trade.net_pnl = (trade.net_pnl or Decimal(0)) + leg_net_pnl
    trade.premium_paid = trade.premium_paid - cost_basis_this_leg
    trade.fees = (trade.fees or Decimal(0)) + exit_fees
    remaining_entry_fees -= entry_fees_this_leg

    # avg_exit is the qty-weighted average price across every leg, not
    # just the leg that happened to close the position — accumulated in
    # risk_params (no dedicated column for "running exit proceeds") and
    # resolved into the trades.avg_exit column only once fully closed.
    # `cum_exit_value` is in money (price * qty * lot_size); dividing by
    # `cum_exit_qty * trade.lot_size` (not `cum_exit_qty` alone, which is
    # lots only — the P5 subagent review's other finding: this used to
    # leave `avg_exit` scaled up by `lot_size`, silently corrupting every
    # downstream R-multiple) recovers the true per-unit weighted price.
    cum_exit_qty_raw = risk_params.get("cum_exit_qty", 0)
    cum_exit_qty_prior = int(cum_exit_qty_raw) if isinstance(cum_exit_qty_raw, int | float) else 0
    cum_exit_qty = cum_exit_qty_prior + execution.filled_qty
    cum_exit_value = Decimal(str(risk_params.get("cum_exit_value", "0"))) + exit_value

    if fully_closed:
        trade.closed_at = now
        trade.avg_exit = cum_exit_value / (cum_exit_qty * trade.lot_size)
        trade.exit_reason = decision.reason
        trade.holding_secs = int((now - trade.opened_at).total_seconds())
        trade.qty_lots = 0
        trade.risk_params = {
            **risk_params,
            "hwm_price": str(hwm_price),
            "remaining_entry_fees": str(remaining_entry_fees),
        }
        # Mirrors context_entry — just the underlying price at close, from
        # the same quote row `mark` already came from (no extra query).
        # Full regime context isn't rebuilt here since no breakdown
        # dimension needs an exit-time regime, only an entry-time one.
        trade.context_exit = {
            "underlying_px": (
                float(latest_quote.underlying_px)
                if latest_quote.underlying_px is not None
                else None
            )
        }
    else:
        trade.qty_lots = remaining_qty
        new_risk_params: dict[str, object] = {
            **risk_params,
            "hwm_price": str(hwm_price),
            "cum_exit_qty": cum_exit_qty,
            "cum_exit_value": str(cum_exit_value),
            "remaining_entry_fees": str(remaining_entry_fees),
        }
        if decision.reason.startswith("PARTIAL_EXIT_R"):
            target = float(decision.reason.removeprefix("PARTIAL_EXIT_R"))
            already_taken = risk_params.get("partial_exits_taken", [])
            taken: set[float] = set(already_taken) if isinstance(already_taken, list) else set()
            taken.add(target)
            new_risk_params["partial_exits_taken"] = list(taken)
        trade.risk_params = new_risk_params

    await event_log.append_event(
        session,
        trade_id=trade.trade_id,
        event_type="CLOSED" if fully_closed else "PARTIAL_EXIT",
        payload={
            "qty": execution.filled_qty,
            "price": str(execution.price),
            "reason": decision.reason,
        },
        ts=now,
    )
    await session.commit()
    return ExitOutcome(
        trade.trade_id, decision.reason, closed=fully_closed, qty_lots=execution.filled_qty
    )


async def _latest_quote(session: AsyncSession, instrument_id: int) -> OptionQuote | None:
    row: OptionQuote | None = await session.scalar(
        select(OptionQuote)
        .where(OptionQuote.instrument_id == instrument_id, OptionQuote.ltp.is_not(None))
        .order_by(OptionQuote.ts.desc())
        .limit(1)
    )
    return row
