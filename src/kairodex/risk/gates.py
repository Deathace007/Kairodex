"""The risk gate chain (ARCHITECTURE.md §11), in the doc's own order:

    kill switch -> breaker state -> session/time window -> event blackout
    -> daily loss -> weekly loss -> drawdown throttle -> max concurrent
    -> exposure % -> correlation cluster -> liquidity -> capital available
    -> 1-lot risk ceiling

Everything except the last stage lives here; "1-lot risk ceiling" is
`kairodex.risk.sizing.size_position`'s `SIZE_EXCEEDS_CEILING`/
`NO_TRADE_MIN_SIZE` rejection, evaluated after the chain approves entry
(sizing needs to know the chain already said yes before it's worth
computing a lot count) — the doc itself shows sizing as a separate code
block from the gate order list, chained conceptually, not literally one
more function in this list.

"no re-entry within N minutes after a loss" (anti-revenge, §11's controls
list) isn't in the doc's explicit numbered order — folded into
`correlation_cluster_gate` here since both gate on "is *this* underlying
currently restricted," the natural place for a same-underlying check to
live rather than inventing a 13th stage the order list doesn't name.

Every gate: `(proposal, account, config, now) -> GateResult`, so the
chain runner can call them uniformly and stop at the first denial.
"""

from __future__ import annotations

import datetime

from kairodex.config.segments import SegmentRiskConfig
from kairodex.risk.types import AccountState, ChainResult, GateResult, TradeProposal


def kill_switch_gate(
    proposal: TradeProposal,
    account: AccountState,
    config: SegmentRiskConfig,
    now: datetime.datetime,
) -> GateResult:
    if account.kill_switch_engaged:
        return GateResult("kill_switch", False, "KILL_SWITCH_ENGAGED")
    return GateResult("kill_switch", True)


def breaker_gate(
    proposal: TradeProposal,
    account: AccountState,
    config: SegmentRiskConfig,
    now: datetime.datetime,
) -> GateResult:
    if account.breaker_status != "ARMED":
        reason = f"BREAKER_TRIPPED:{account.breaker_reason or 'unspecified'}"
        return GateResult("breaker_state", False, reason)
    return GateResult("breaker_state", True)


def session_window_gate(
    proposal: TradeProposal,
    account: AccountState,
    config: SegmentRiskConfig,
    now: datetime.datetime,
) -> GateResult:
    """`account.session_open` is the caller's to compute from
    `trading_calendar` — that table exists (P0) but nothing populates it
    yet (a real, separate gap: no vendor holiday/session-hours sync has
    been built). This gate fails closed on that: no calendar data means
    `session_open` should default to False, not True."""
    if not account.session_open:
        return GateResult("session_window", False, "OUTSIDE_SESSION_WINDOW")
    return GateResult("session_window", True)


def event_blackout_gate(
    proposal: TradeProposal,
    account: AccountState,
    config: SegmentRiskConfig,
    now: datetime.datetime,
) -> GateResult:
    """Always allows — no earnings/macro event calendar integration
    exists yet (a real, documented gap, not a silent no-op pretending to
    be real: this function exists so the chain's *shape* matches the doc
    exactly, and so a future real implementation only has to change this
    one function, not the chain's structure)."""
    return GateResult("event_blackout", True)


def daily_loss_gate(
    proposal: TradeProposal,
    account: AccountState,
    config: SegmentRiskConfig,
    now: datetime.datetime,
) -> GateResult:
    limit = config.daily_loss_limit_pct * float(account.equity)
    if float(-account.daily_pnl) >= limit:
        return GateResult("daily_loss", False, "DAILY_LOSS_LIMIT_REACHED")
    return GateResult("daily_loss", True)


def weekly_loss_gate(
    proposal: TradeProposal,
    account: AccountState,
    config: SegmentRiskConfig,
    now: datetime.datetime,
) -> GateResult:
    limit = config.weekly_loss_limit_pct * float(account.equity)
    if float(-account.weekly_pnl) >= limit:
        return GateResult("weekly_loss", False, "WEEKLY_LOSS_LIMIT_REACHED")
    return GateResult("weekly_loss", True)


def drawdown_throttle_gate(
    proposal: TradeProposal,
    account: AccountState,
    config: SegmentRiskConfig,
    now: datetime.datetime,
) -> GateResult:
    """A hard stop at `max_drawdown_pct` — distinct from `sizing.risk_multiplier`'s
    smooth tapering below that threshold; this is the backstop once
    tapering alone isn't enough."""
    if account.high_water_mark <= 0:
        return GateResult("drawdown_throttle", True)
    drawdown_pct = float(account.high_water_mark - account.equity) / float(account.high_water_mark)
    if drawdown_pct >= config.max_drawdown_pct:
        return GateResult("drawdown_throttle", False, "MAX_DRAWDOWN_REACHED")
    return GateResult("drawdown_throttle", True)


def max_concurrent_gate(
    proposal: TradeProposal,
    account: AccountState,
    config: SegmentRiskConfig,
    now: datetime.datetime,
) -> GateResult:
    if account.open_positions_count >= config.max_concurrent:
        return GateResult("max_concurrent", False, "MAX_CONCURRENT_POSITIONS_REACHED")
    return GateResult("max_concurrent", True)


def exposure_gate(
    proposal: TradeProposal,
    account: AccountState,
    config: SegmentRiskConfig,
    now: datetime.datetime,
) -> GateResult:
    proposed_premium = proposal.premium_per_lot * proposal.lot_size
    projected_exposure = float(account.total_exposure) + float(proposed_premium)
    if account.equity <= 0:
        return GateResult("exposure", False, "NO_EQUITY")
    if projected_exposure / float(account.equity) > config.exposure_cap_pct:
        return GateResult("exposure", False, "EXPOSURE_CAP_EXCEEDED")
    return GateResult("exposure", True)


def correlation_cluster_gate(
    proposal: TradeProposal,
    account: AccountState,
    config: SegmentRiskConfig,
    now: datetime.datetime,
) -> GateResult:
    """A same-underlying position already open counts as one cluster
    (a first-pass proxy for true cross-underlying correlation clustering
    — a real correlation matrix across the whole watchlist doesn't exist
    yet; P2's `index_correlation` feature is underlying-vs-benchmark, not
    underlying-vs-underlying). Also enforces the anti-revenge rule (§11's
    controls list, not in the doc's numbered gate order — see module
    docstring for why it lives here): no re-entry on the same underlying
    within `reentry_cooldown_minutes` of a loss on it."""
    if proposal.underlying_symbol in account.open_underlyings:
        return GateResult("correlation_cluster", False, "ALREADY_OPEN_ON_UNDERLYING")
    last_loss = account.last_loss_ts_by_underlying.get(proposal.underlying_symbol)
    if last_loss is not None:
        cooldown = datetime.timedelta(minutes=config.reentry_cooldown_minutes)
        if now - last_loss < cooldown:
            return GateResult("correlation_cluster", False, "REENTRY_COOLDOWN_ACTIVE")
    return GateResult("correlation_cluster", True)


def liquidity_gate(
    proposal: TradeProposal,
    account: AccountState,
    config: SegmentRiskConfig,
    now: datetime.datetime,
) -> GateResult:
    """Fails closed on unknown liquidity (`None`) — an unpriced/unquoted
    contract isn't "probably fine," it's untradeable until proven
    otherwise. Also rejects an incomplete chain snapshot outright
    (ARCHITECTURE.md §7: "incomplete chains are unusable for signals")."""
    if not proposal.chain_complete:
        return GateResult("liquidity", False, "INCOMPLETE_CHAIN")
    if proposal.liquidity_score is None:
        return GateResult("liquidity", False, "LIQUIDITY_UNKNOWN")
    if proposal.liquidity_score < config.min_liquidity_score:
        return GateResult("liquidity", False, "LIQUIDITY_TOO_LOW")
    return GateResult("liquidity", True)


def capital_available_gate(
    proposal: TradeProposal,
    account: AccountState,
    config: SegmentRiskConfig,
    now: datetime.datetime,
) -> GateResult:
    """The affordability constraint (§11 — a policy cap on single-position
    premium relative to equity, `premium * lot_size <= max_premium_pct *
    equity`, binding constantly on a small book).

    Does NOT separately check "is there enough uncommitted capital left
    after existing open positions" — caught by this gate's own tests: with
    `exposure_gate` running earlier in the chain and guaranteeing
    `(total_exposure + premium) <= exposure_cap_pct * equity <= equity`
    (exposure_cap_pct is always a fraction <= 1), `premium <= equity -
    total_exposure` already holds by the time execution reaches here.
    Reaching this gate at all is proof there's enough uncommitted capital —
    a second check for it would be unreachable dead code, not defense in
    depth."""
    proposed_premium = float(proposal.premium_per_lot * proposal.lot_size)
    equity = float(account.equity)
    if equity <= 0:
        return GateResult("capital_available", False, "NO_EQUITY")
    if proposed_premium > config.max_premium_pct * equity:
        return GateResult("capital_available", False, "EXCEEDS_MAX_PREMIUM_PCT")
    return GateResult("capital_available", True)


GATE_CHAIN = (
    kill_switch_gate,
    breaker_gate,
    session_window_gate,
    event_blackout_gate,
    daily_loss_gate,
    weekly_loss_gate,
    drawdown_throttle_gate,
    max_concurrent_gate,
    exposure_gate,
    correlation_cluster_gate,
    liquidity_gate,
    capital_available_gate,
)


def run_gate_chain(
    proposal: TradeProposal,
    account: AccountState,
    config: SegmentRiskConfig,
    now: datetime.datetime | None = None,
) -> ChainResult:
    """Runs `GATE_CHAIN` in order, stopping at the first denial —
    "a chain of independent gates" (§11), not "run everything and see.\""""
    now = now if now is not None else datetime.datetime.now(datetime.UTC)
    evaluated = []
    for gate in GATE_CHAIN:
        result = gate(proposal, account, config, now)
        evaluated.append(result)
        if not result.allowed:
            return ChainResult(allowed=False, evaluated=tuple(evaluated))
    return ChainResult(allowed=True, evaluated=tuple(evaluated))
