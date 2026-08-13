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
import logging
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from kairodex.config.segments import SegmentRiskConfig
from kairodex.core.enums import Market, Segment, Side
from kairodex.core.sessions import session_length_secs, session_seconds_between
from kairodex.data.types import ChainSnapshot, Tick
from kairodex.engine import event_log
from kairodex.engine.monitor import Position, evaluate_exits
from kairodex.execution.simulator import ExecutionPort, ExecutionResult
from kairodex.execution.synthetic_quote import SPREAD_PCT, synthesize_quote
from kairodex.execution.types import OrderRequest, QuoteSnapshot
from kairodex.features import loader as feature_loader
from kairodex.features import registry as feature_registry
from kairodex.risk.gates import run_gate_chain
from kairodex.risk.sizing import size_position
from kairodex.risk.types import AccountState, TradeProposal
from kairodex.store.models import Fill, Instrument, OptionQuote, Order, PositionMark, Signal, Trade
from kairodex.strategy.contract_selector import ContractCandidate, SelectionResult, select_contract
from kairodex.strategy.protocol import Strategy
from kairodex.strategy.scorer import ConfluenceScorer
from kairodex.strategy.types import MarketContext

logger = logging.getLogger(__name__)

# 0.30 -> 0.20 on 2026-08-14, the first time this was set from outcomes
# rather than convention. Sweeney-style excursion analysis over the 24 NSE
# trades closed since the 08-11 reset (PROGRESS.md §19): no winner ever
# drew down more than 9.2% in its first 30 session-minutes, while losers
# reached -26.6%; every winner reached at least +12.2% MFE against a
# median loser peak of +3.4%. A 30% stop sits far outside the region where
# the two populations separate — it cut only 2 of 16 losers, leaving the
# rest to the scratch rule 90 minutes later.
#
# MUST be changed together with `_DEFAULT_R_MULTIPLE_TARGETS` below: R is
# defined as the stop distance, so every partial-exit rung moves with this
# number. Tightening the stop alone silently tightens profit-taking too,
# which is why "tighter stop" on its own measured WORSE in replay
# (-5,297 vs -3,002) while the same stop with the ladder widened measured
# better (+7,311).
_DEFAULT_STOP_LOSS_PCT = 0.20
_DEFAULT_MAX_QUOTE_AGE_MS = 2000
_DEFAULT_PROFIT_TARGET_PCT = 1.0  # ponytail: exit at 2x entry premium
# (100% gain) — first-pass, same status as the stop-loss/target-delta
# constants above: a common options-buying convention, not backtested yet.
# Partial exits at 2R only, from 2026-08-14. The history matters here
# because this rung has now moved twice on opposite evidence:
#
#   - until 2026-08-10 the first rung was 1R (+30% of premium at the then
#     30% stop), judged too far out to reach on the first ten trades;
#   - 2026-08-10 added 0.5R/1R/2R, banking half the position at +15%;
#   - 2026-08-14 removes everything below 2R, on 24 closed trades.
#
# What the 24 trades say (PROGRESS.md §19): only 8 of 24 ever reached
# +15%, 3 reached +30%, and 1 reached +60%. The ladder therefore halved
# the position on precisely the third of trades that carry the entire
# book, while every loser exited at full size — an inverted payoff on an
# options-buying strategy, whose whole premise is convexity. Trade 62
# (TCS) is the clearest case: 12 of its 13 lots were sold inside 7
# minutes and the last lot ran to +106%.
#
# 2R against the 20% stop above fires at +40% of premium. Deliberately
# NOT expressed as a percentage: R-multiples are the correct unit for
# risk-relative profit-taking, and a second unit system here would be one
# more thing to keep in sync. The coupling is real and is documented on
# `_DEFAULT_STOP_LOSS_PCT` instead.
#
# Honest limit: removing the ladder outright is worth +Rs 8,388 in replay
# but 87% of that is one trade, and it fails a leave-one-out check. The
# 2R-only variant, paired with the 20% stop and the 45-minute scratch
# window, beats the live ruleset even with the two dominant winners
# deleted (+4,026), which is why this is the shape that shipped.
_DEFAULT_R_MULTIPLE_TARGETS = (2.0,)
# Theta guard in SESSION seconds now, not wall-clock — the per-segment
# `max_holding_sessions` knob converts. Kept as the fallback for a trade
# row written before that config existed.
_DEFAULT_MAX_HOLDING_SECS = 3 * 6 * 3600


@dataclass(frozen=True, slots=True)
class TickOutcome:
    signal_id: int
    taken: bool
    reject_stage: str | None
    reject_reason: str | None
    trade_id: int | None = None


def _candidates_from_chain(
    quotes: list[Tick], expiry: datetime.date, *, synthetic_quotes: bool = False
) -> list[ContractCandidate]:
    """`quotes` is a list of `kairodex.data.types.Tick` from one
    `ChainSnapshot`'s legs — `expiry` is that snapshot's own expiry
    (every leg in it shares one), passed in rather than guessed, since
    `Tick` itself carries no `expiry` field (see `FeatureContext`'s
    docstring in `kairodex.features.types` for why).

    `synthetic_quotes` models a book from last price where the vendor
    publishes none (US/LSE — see execution.synthetic_quote). It is an
    explicit per-market opt-in rather than "synthesize whenever bid/ask
    happen to be missing": the latter would mean an NSE feed hiccup
    silently switched a real-book segment onto modelled prices, which is a
    far worse failure than skipping a contract for one tick.
    """
    out = []
    for q in quotes:
        if q.strike is None or q.option_type is None:
            continue
        bid, ask, bid_sz, ask_sz = q.bid, q.ask, q.bid_sz, q.ask_sz
        if bid is None or ask is None:
            if not synthetic_quotes:
                continue
            modelled = synthesize_quote(q.ltp, q.volume)
            if modelled is None:
                continue
            bid, ask = modelled.bid, modelled.ask
            bid_sz, ask_sz = modelled.bid_sz, modelled.ask_sz
        out.append(
            ContractCandidate(
                instrument_id=0,  # resolved separately, by instrument_key -> DB lookup
                strike=q.strike,
                option_type=q.option_type,
                expiry=expiry,
                bid=bid,
                ask=ask,
                delta=q.delta,
                bid_sz=bid_sz,
                ask_sz=ask_sz,
                oi=q.oi,
                quote_ts=q.ts,
            )
        )
    return out


def _select_across_expiries(
    chain: list[ChainSnapshot],
    direction: Side,
    *,
    spot: Decimal,
    equity: Decimal,
    max_premium_pct: float,
    lot_size: int,
    as_of: datetime.date,
    synthetic_quotes: bool,
) -> SelectionResult:
    """Try every loaded expiry snapshot, nearest first, not just the single
    nearest one — pulled out of `run_entry_tick` so this exact loop has a
    unit test, since `run_entry_tick` itself is DB-touching and only ever
    exercised on the VM.

    Bug found live 2026-08-06: the previous code committed to
    `min(chain, key=expiry)` and gave up if THAT snapshot's DTE fell
    outside `select_contract`'s own min_dte/max_dte window — even when a
    second, perfectly good snapshot was sitting unused in the same
    `feature_ctx.chain`. This was the dominant reason us_index never
    traded: SPY/QQQ/IWM trade near-daily expiries, so the nearest one is
    almost always 0 DTE (today), which `min_dte=1` correctly rejects,
    while the very next snapshot (traced live: SPY/QQQ/IWM all had one at
    1 DTE) would select cleanly. DTE bounds are still exactly
    `select_contract`'s own to enforce; this only makes sure every loaded
    expiry actually gets asked.

    Returns the first snapshot's own failing `SelectionResult` when
    nothing across the whole chain selects, so the caller's existing
    `selection.selected is None` check needs no change — same contract a
    single-snapshot call already had."""
    first: SelectionResult | None = None
    informative: SelectionResult | None = None
    for snapshot in sorted(chain, key=lambda s: s.expiry):
        candidates = _candidates_from_chain(
            snapshot.quotes, snapshot.expiry, synthetic_quotes=synthetic_quotes
        )
        trial = select_contract(
            candidates,
            direction,
            spot=spot,
            equity=equity,
            max_premium_pct=max_premium_pct,
            lot_size=lot_size,
            as_of=as_of,
        )
        if first is None:
            first = trial
        # NO_CANDIDATES_IN_EXPIRY_WINDOW just means "this snapshot's DTE is
        # outside the window" — for a near-daily-expiry underlying that is
        # the *expected* answer for the 0-DTE snapshot and says nothing
        # about why the trade didn't happen. Reporting it (the nearest
        # snapshot's reason) masked the real blocker on the expiry that
        # actually qualified. Verified live 2026-08-06: IWM's 0-DTE gave
        # NO_CANDIDATES while its 1-DTE gave NO_CONTRACT_NEAR_TARGET_DELTA,
        # and the signals table recorded the former — which sent a whole
        # investigation after an expiry-window problem that did not exist.
        # ARCHITECTURE.md §11 makes this a correctness issue, not cosmetics:
        # "every denial is written to signals ... The rejections are
        # training data." Prefer a reason from an expiry that cleared the
        # DTE window and still failed for a substantive reason.
        if informative is None and trial.reason != "NO_CANDIDATES_IN_EXPIRY_WINDOW":
            informative = trial
        if trial.selected is not None and trial.mid_price is not None:
            return trial
    assert first is not None  # caller already checked `feature_ctx.chain` is non-empty
    return informative or first


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

    # Conviction floor, before any of the work below. A concurrent slot is
    # a scarce resource: taking a weak setup does not just risk that trade,
    # it locks out every better one until it closes. Live 2026-08-07 that
    # is exactly what happened — nse_stock filled 4 of 5 slots in the
    # opening tick (one at confidence 0.1366) and then rejected 12,696
    # subsequent signals on MAX_CONCURRENT, some scoring as high as 0.7344.
    # Rejected here rather than skipped entirely, because a below-threshold
    # signal is real training data (ARCHITECTURE.md §11) — unlike an
    # out-of-hours one, it says something about the setup, not the clock.
    if result.confidence < config.min_confidence:
        signal.reject_stage, signal.reject_reason = "confidence", "BELOW_MIN_CONFIDENCE"
        await session.commit()
        return TickOutcome(signal.signal_id, False, signal.reject_stage, signal.reject_reason)

    if feature_ctx.spot is None or not feature_ctx.chain:
        signal.reject_stage, signal.reject_reason = "contract_selection", "NO_CHAIN_DATA"
        await session.commit()
        return TickOutcome(signal.signal_id, False, signal.reject_stage, signal.reject_reason)

    # LSE (the only US options vendor) publishes no book at all, so US
    # candidates are modelled from last price — see execution.synthetic_quote
    # for the three rules that keep that honest. NSE has real depth and must
    # never take this path.
    synthetic_quotes = segment.market is Market.US

    lot_size = 25 if segment.market.value == "nse" else 100  # ponytail: instrument_specs (P0
    # table, SCD-2 lot size) isn't wired into this path yet — a fixed per-market default
    # stands in; real per-underlying lot sizes vary (see docs/PROGRESS.md's next-steps).
    # Resolved *before* select_contract, not after: the affordability filter inside it
    # depends on the real lot_size (premium * lot_size <= max_premium_pct * equity) —
    # using the wrong lot_size there would silently pass contracts that are actually
    # unaffordable once sized for real.

    selection = _select_across_expiries(
        feature_ctx.chain,
        result.direction,
        spot=Decimal(str(feature_ctx.spot)),
        equity=account.equity,
        max_premium_pct=config.max_premium_pct,
        lot_size=lot_size,
        as_of=now.date(),
        synthetic_quotes=synthetic_quotes,
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
        trade_id=0, instrument_id=instrument_id, side=Side.BUY, qty=sizing.lots,
        lot_size=lot_size,
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
        config=config,
        spot=feature_ctx.spot,
        values=values,
        synthetic_quote=synthetic_quotes,
    )
    signal.decision = "TAKEN"
    await session.commit()
    return TickOutcome(signal.signal_id, True, None, None, trade_id=trade_id)


async def _resolve_leg_instrument_id(
    session: AsyncSession, underlying: Instrument, candidate: ContractCandidate
) -> int | None:
    """The (underlying, strike, type, expiry) key is not unique in
    `instruments` — two rows for one real contract were reachable, and
    this used to take whichever one the database handed back first. That
    silently split the trade's life across two identities: the candidate
    was priced off the row the chain poll writes quotes to, and the trade
    was then opened against the other one, which had had no quote since
    2026-08-05 — so `_latest_quote` returned a five-day-old price or
    nothing at all, and stop-losses could not fire. Every open nse_stock
    position on 2026-08-10 was in that state.

    `data.ingest.upsert_instrument` now matches on the provider key, so
    the duplicates cannot be recreated. This orders by most-recently-
    quoted anyway: the same money path must not go back to picking
    arbitrarily if a duplicate ever reappears from some other direction,
    and "the row the feed is actually writing to" is the only defensible
    tiebreak."""
    last_quote = (
        select(OptionQuote.instrument_id, func.max(OptionQuote.ts).label("last_ts"))
        .group_by(OptionQuote.instrument_id)
        .subquery()
    )
    row = await session.scalar(
        select(Instrument)
        .outerjoin(last_quote, last_quote.c.instrument_id == Instrument.instrument_id)
        .where(
            Instrument.exchange == underlying.exchange,
            Instrument.underlying_symbol == underlying.symbol,
            Instrument.strike == candidate.strike,
            Instrument.option_type == candidate.option_type,
            Instrument.expiry == candidate.expiry,
        )
        .order_by(last_quote.c.last_ts.desc().nullslast())
        .limit(1)
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
    config: SegmentRiskConfig,
    spot: float | None,
    values: dict[str, float],
    synthetic_quote: bool = False,
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
            # This fill was priced off a modelled book, not an observed one
            # (execution.synthetic_quote). Recorded per-trade rather than
            # inferred from the segment later, because the assumption can
            # change while old trades keep whatever they were actually
            # filled under — analysis must never silently mix the two.
            "synthetic_quote": synthetic_quote,
            "synthetic_spread_pct": float(SPREAD_PCT) if synthetic_quote else None,
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
            # Session seconds, not wall-clock: `max_holding_sessions` x
            # the length of one regular session for this market. A
            # 3-calendar-day guard gave a Thursday entry two sessions and
            # then forced it out at Monday's opening bell.
            "max_holding_secs": int(
                config.max_holding_sessions * session_length_secs(segment.market)
            ),
            "scratch_after_secs": config.scratch_exit_after_minutes * 60,
            "scratch_min_mfe_pct": config.scratch_exit_min_mfe_pct,
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
    scratch_after_raw = risk_params.get("scratch_after_secs")
    scratch_after_secs = (
        int(scratch_after_raw) if isinstance(scratch_after_raw, int | float) else None
    )
    scratch_min_mfe_raw = risk_params.get("scratch_min_mfe_pct", 0.08)
    scratch_min_mfe_pct = (
        float(scratch_min_mfe_raw) if isinstance(scratch_min_mfe_raw, int | float) else 0.08
    )
    # Both time-based rules measure open-market seconds, not wall-clock —
    # see core.sessions.session_seconds_between for what that fixed.
    held_session_secs = session_seconds_between(trade.segment.market, trade.opened_at, now)
    # The traded contract's own expiry, so a position can never be carried
    # into it. `expiry_exit_check` abstains when this is None rather than
    # guessing, and a missing instrument row is already impossible here —
    # `trade.instrument_id` is an FK.
    expiry = await session.scalar(
        select(Instrument.expiry).where(Instrument.instrument_id == trade.instrument_id)
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
        held_session_secs=held_session_secs,
        expiry=expiry,
        scratch_after_secs=scratch_after_secs,
        scratch_min_mfe_pct=scratch_min_mfe_pct,
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
    quote = _exit_quote(trade.segment, latest_quote, mark)
    if quote is None:
        quote_age_s = (now - latest_quote.ts).total_seconds()
        logger.warning(
            "trade %d: %s has no modellable book (ltp=%s volume=%s) — position still open",
            trade.trade_id,
            decision.reason,
            mark,
            latest_quote.volume,
        )
        await event_log.append_event(
            session,
            trade_id=trade.trade_id,
            event_type="EXIT_FAILED",
            payload={
                "attempted": decision.reason,
                "reject_reason": "NO_SYNTHETIC_BOOK",
                "qty": decision.qty_lots,
                "quote_age_s": round(quote_age_s, 1),
            },
            ts=now,
        )
        await session.commit()
        return ExitOutcome(trade.trade_id, f"{decision.reason}_FILL_FAILED", closed=False)
    order_request = OrderRequest(
        trade_id=trade.trade_id,
        instrument_id=trade.instrument_id,
        side=Side.SELL,
        qty=decision.qty_lots,
        lot_size=trade.lot_size,
    )
    execution = await broker.execute(order_request, quote, now, attempt=1)
    if execution.rejected or execution.filled_qty <= 0:
        # An exit that cannot fill is a risk event, not a non-event: the
        # position stays open past whatever level just told it to leave.
        # This used to return silently — no log line, no DB row, nothing —
        # and live 2026-08-07 that hid a real stop-loss breach: trade 2
        # (HDFCBANK 750 C) marked at 8.75 then 8.70 against an 8.75 stop on
        # two consecutive ticks, both attempts were rejected STALE_QUOTE,
        # and the position simply stayed open. It survived only because the
        # price happened to recover. Nothing anywhere recorded that the
        # stop had failed to execute.
        #
        # Logged AND written to the event log, because the event log is
        # this system's record of truth for a trade's life (Principle 2)
        # and "we were told to exit and could not" belongs in it.
        quote_age_s = (now - latest_quote.ts).total_seconds()
        logger.warning(
            "trade %d: %s could not fill (%s) — quote %.1fs old, position still open",
            trade.trade_id,
            decision.reason,
            execution.reject_reason,
            quote_age_s,
        )
        await event_log.append_event(
            session,
            trade_id=trade.trade_id,
            event_type="EXIT_FAILED",
            payload={
                "attempted": decision.reason,
                "reject_reason": execution.reject_reason,
                "qty": decision.qty_lots,
                "quote_age_s": round(quote_age_s, 1),
            },
            ts=now,
        )
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


def _exit_quote(
    segment: Segment, latest_quote: OptionQuote, mark: Decimal
) -> QuoteSnapshot | None:
    """The book to price an exit against — modelled for US, observed for NSE.

    `bid_sz`/`ask_sz` used to fall back to **0** when the vendor published
    none, and `compute_fill` fills `floor(partial_fill_alpha * size)` = 0
    at any size below 4. LSE publishes no bid, ask, or sizes at all (§13c),
    so **every US exit was rejected `NO_LIQUIDITY_AT_TOP_OF_BOOK`, always** —
    no US position had ever been exitable. §13e built `synthesize_quote`
    for the entry path and §13c noted the exit path "already degrades
    gracefully" via `bid=latest_quote.bid or mark`; that fixed the *price*
    and left the *size* at zero, which can never fill. It surfaced the
    moment the intraday rule started demanding exits that had to work.

    Symmetry with entries buys a real guarantee under the intraday rule:
    `_candidates_from_chain` will not offer a contract whose modelled size
    cannot fill a lot, and same-day volume only ever grows — so a position
    that was enterable this session is exitable this session.

    Still gated on `Market.US` rather than "synthesize whenever sizes are
    missing", for §13e's reason: an NSE feed hiccup must never quietly move
    a real-book segment onto modelled prices."""
    if segment.market is not Market.US:
        return QuoteSnapshot(
            bid=latest_quote.bid or mark,
            ask=latest_quote.ask or mark,
            bid_sz=latest_quote.bid_sz or 0,
            ask_sz=latest_quote.ask_sz or 0,
            quote_ts=latest_quote.ts,
            oi=latest_quote.oi,
        )
    if latest_quote.bid is not None and latest_quote.ask is not None:
        return QuoteSnapshot(
            bid=latest_quote.bid,
            ask=latest_quote.ask,
            bid_sz=latest_quote.bid_sz or 0,
            ask_sz=latest_quote.ask_sz or 0,
            quote_ts=latest_quote.ts,
            oi=latest_quote.oi,
        )
    modelled = synthesize_quote(mark, latest_quote.volume)
    if modelled is None:
        return None
    return QuoteSnapshot(
        bid=modelled.bid,
        ask=modelled.ask,
        bid_sz=modelled.bid_sz,
        ask_sz=modelled.ask_sz,
        quote_ts=latest_quote.ts,
        oi=latest_quote.oi,
    )


async def _latest_quote(session: AsyncSession, instrument_id: int) -> OptionQuote | None:
    row: OptionQuote | None = await session.scalar(
        select(OptionQuote)
        .where(OptionQuote.instrument_id == instrument_id, OptionQuote.ltp.is_not(None))
        .order_by(OptionQuote.ts.desc())
        .limit(1)
    )
    return row
