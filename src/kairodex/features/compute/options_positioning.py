"""OI change & PCR, net gamma exposure, liquidity score — ARCHITECTURE.md
§9 launch-set bullets 13-15, the last group."""

from __future__ import annotations

from kairodex.core.enums import Market
from kairodex.data.types import ChainSnapshot, Tick
from kairodex.execution.synthetic_quote import synthesize_quote
from kairodex.features.registry import register
from kairodex.features.types import FeatureContext, Fidelity, Tier

_CONTRACT_MULTIPLIER = 100  # standard lot size assumption; real per-instrument
# lot_size lives on InstrumentRecord but isn't threaded through FeatureContext
# yet — see docs/PROGRESS.md P2 §8 for the pattern this would follow.
_LIQUIDITY_SPREAD_K = 10.0
_LIQUIDITY_DEPTH_K = 100.0
_LIQUIDITY_OI_K = 1000.0
_LIQUIDITY_VOLUME_K = 500.0


def _all_quotes(snapshots: list[ChainSnapshot]) -> list[Tick]:
    return [q for snap in snapshots for q in snap.quotes]


@register(
    name="oi_pcr",
    inputs=["CHAIN"],
    tier=Tier.T1,
    fidelity=Fidelity.EXACT,
    backtestable={"nse": True, "us": True},
    cost_ms=1,
)
def oi_pcr(ctx: FeatureContext) -> float | None:
    """Put OI / call OI, aggregated across every expiry present in
    `ctx.chain` (not just the front month) — a chain-wide positioning
    read; a caller wanting per-expiry PCR passes a `ctx.chain` with just
    that one snapshot."""
    quotes = _all_quotes(ctx.chain)
    put_oi = sum(float(q.oi) for q in quotes if q.option_type == "P" and q.oi is not None)
    call_oi = sum(float(q.oi) for q in quotes if q.option_type == "C" and q.oi is not None)
    if call_oi == 0:
        return None
    return put_oi / call_oi


@register(
    name="oi_change",
    inputs=["CHAIN", "PRIOR_CHAIN"],
    tier=Tier.T1,
    fidelity=Fidelity.EXACT,
    backtestable={"nse": True, "us": True},
    cost_ms=1,
)
def oi_change(ctx: FeatureContext) -> float | None:
    """Net OI change since `ctx.prior_chain`, normalized by total prior OI
    (a % change, not a raw contract count — comparable across underlyings
    of very different size). Matched leg-to-leg by `instrument_key` (the
    natural join key `Tick` already carries); a leg present in one
    snapshot but not the other contributes its whole OI as the change,
    same as any new/expired position would."""
    current = {q.instrument_key: float(q.oi) for q in _all_quotes(ctx.chain) if q.oi is not None}
    prior = {
        q.instrument_key: float(q.oi) for q in _all_quotes(ctx.prior_chain) if q.oi is not None
    }
    if not current or not prior:
        return None
    total_prior = sum(prior.values())
    if total_prior == 0:
        return None
    all_keys = set(current) | set(prior)
    total_change = sum(current.get(k, 0.0) - prior.get(k, 0.0) for k in all_keys)
    return total_change / total_prior


@register(
    name="net_gamma_exposure",
    inputs=["CHAIN"],
    tier=Tier.T1,
    fidelity=Fidelity.ESTIMATE,
    backtestable={"nse": True, "us": True},
    cost_ms=2,
)
def net_gamma_exposure(
    ctx: FeatureContext, *, contract_multiplier: int = _CONTRACT_MULTIPLIER
) -> float | None:
    """Dealer gamma exposure proxy, the common public convention (as used
    by e.g. SqueezeMetrics/SpotGamma-style GEX): assumes dealers are net
    long call gamma and net short put gamma from customer order flow —
    a real, debated assumption, not a measured fact, hence ESTIMATE
    fidelity (the spec's own bullet name says "estimate"). GEX = spot^2 *
    0.01 * contract_multiplier * sum(call_gamma*call_OI - put_gamma*put_OI)."""
    if ctx.spot is None:
        return None
    quotes = _all_quotes(ctx.chain)
    total = 0.0
    found = False
    for q in quotes:
        if q.oi is None or q.gamma is None:
            continue
        found = True
        sign = 1.0 if q.option_type == "C" else -1.0
        total += sign * float(q.gamma) * float(q.oi)
    if not found:
        return None
    return total * contract_multiplier * ctx.spot**2 * 0.01


@register(
    name="liquidity_score",
    inputs=["CHAIN"],
    tier=Tier.T1,
    fidelity=Fidelity.ESTIMATE,
    backtestable={"nse": True, "us": True},
    cost_ms=1,
)
def liquidity_score(ctx: FeatureContext) -> float | None:
    """Composite of spread/depth/OI/volume for the nearest-ATM call on the
    front-month expiry — a per-leg concept computed as an underlying-level
    proxy (the single most relevant contract), same simplification
    `term_structure`/`iv_skew` make picking "the" expiry. ponytail: a true
    per-leg liquidity_score, keyed to a specific option instrument_id
    rather than the underlying, is the natural upgrade once
    feature_vectors writes are wired to target option legs directly (P3).
    Each sub-score uses a saturating x/(x+K) curve (0..1, asymptotic) so
    it self-scales without a hardcoded "good" absolute threshold; the
    average of the four is arbitrary-but-documented, not derived.

    US-only: falls back to the same modelled book `_candidates_from_chain`
    (kairodex.engine.orchestrator) synthesizes for LSE, which publishes no
    bid/ask at all. Missing this half was a real bug, not a stricter gate
    working as intended: `risk.gates.liquidity_gate` reads exactly this
    value and fails closed on `None` ("an unpriced contract isn't
    'probably fine'"), which is the right call against a genuinely unknown
    book — but here the book was knowable, just not where this function
    was looking. Before this fix, contract selection could clear (once
    candidates existed) and every single signal still died one gate later
    on a feature that could never be anything but `None` for this vendor
    (live 2026-08-06: 951 of ~1,100 recent us_stock signals). Gated the
    same explicit way as candidate synthesis — `Market.US` only, never a
    blanket "whenever bid/ask happen to be missing" — so an NSE feed
    hiccup can't silently start scoring liquidity on fiction."""
    if not ctx.chain or ctx.spot is None:
        return None
    front = min(ctx.chain, key=lambda s: s.expiry)
    calls = [q for q in front.quotes if q.option_type == "C" and q.strike is not None]
    if not calls:
        return None
    atm = min(calls, key=lambda q: abs(float(q.strike) - ctx.spot))  # type: ignore[arg-type,operator]
    raw_bid, raw_ask, bid_sz, ask_sz = atm.bid, atm.ask, atm.bid_sz, atm.ask_sz
    if raw_bid is None or raw_ask is None or raw_bid <= 0:
        if ctx.segment.market is not Market.US:
            return None
        modelled = synthesize_quote(atm.ltp, atm.volume)
        if modelled is None:
            return None
        raw_bid, raw_ask = modelled.bid, modelled.ask
        bid_sz, ask_sz = modelled.bid_sz, modelled.ask_sz
    bid, ask = float(raw_bid), float(raw_ask)
    mid = (bid + ask) / 2
    if mid <= 0:
        return None
    spread_pct = (ask - bid) / mid
    spread_score = 1.0 / (1.0 + spread_pct * _LIQUIDITY_SPREAD_K)

    depth = float(bid_sz or 0) + float(ask_sz or 0)
    depth_score = depth / (depth + _LIQUIDITY_DEPTH_K)
    oi_val = float(atm.oi or 0)
    oi_score = oi_val / (oi_val + _LIQUIDITY_OI_K)
    volume = float(atm.volume or 0)
    volume_score = volume / (volume + _LIQUIDITY_VOLUME_K)

    return (spread_score + depth_score + oi_score + volume_score) / 4.0
