"""IV rank/percentile, IV skew, term structure — ARCHITECTURE.md §9
launch-set bullets 10-12."""

from __future__ import annotations

from kairodex.data.types import Tick
from kairodex.features.registry import register
from kairodex.features.types import FeatureContext, Fidelity, Tier

_SKEW_TARGET_DELTA = 0.25


@register(
    name="iv_rank",
    inputs=["IV_HISTORY"],
    tier=Tier.T1,
    fidelity=Fidelity.EXACT,
    backtestable={"nse": True, "us": True},
    cost_ms=1,
)
def iv_rank(ctx: FeatureContext) -> float | None:
    """(current - min) / (max - min) over `ctx.iv_history` — 0..1, where
    the window's own min/max include the current point (the standard "IV
    Rank" definition; contrast with `iv_percentile` below, which excludes
    it to avoid the tautology of "is X below X")."""
    if len(ctx.iv_history) < 2:
        return None
    values = [v for _, v in ctx.iv_history]
    current = values[-1]
    lo, hi = min(values), max(values)
    if hi == lo:
        return None
    return (current - lo) / (hi - lo)


@register(
    name="iv_percentile",
    inputs=["IV_HISTORY"],
    tier=Tier.T1,
    fidelity=Fidelity.EXACT,
    backtestable={"nse": True, "us": True},
    cost_ms=1,
)
def iv_percentile(ctx: FeatureContext) -> float | None:
    """Fraction of the *prior* history strictly below the current IV
    reading."""
    if len(ctx.iv_history) < 2:
        return None
    values = [v for _, v in ctx.iv_history]
    current = values[-1]
    history = values[:-1]
    if not history:
        return None
    below = sum(1 for v in history if v < current)
    return below / len(history)


@register(
    name="iv_skew",
    inputs=["CHAIN"],
    tier=Tier.T1,
    fidelity=Fidelity.EXACT,
    backtestable={"nse": True, "us": True},
    cost_ms=2,
)
def iv_skew(ctx: FeatureContext, *, target_delta: float = _SKEW_TARGET_DELTA) -> float | None:
    """25-delta skew (put IV - call IV) on the nearest expiry in the
    chain — the standard convention for "is the market paying up for
    downside protection." Needs vendor delta on each leg; returns None
    (not a wrong number) when delta isn't populated rather than falling
    back to a moneyness-only proxy silently."""
    if not ctx.chain:
        return None
    front = min(ctx.chain, key=lambda s: s.expiry)
    call_iv = _iv_near_delta(front.quotes, "C", target_delta)
    put_iv = _iv_near_delta(front.quotes, "P", -target_delta)
    if call_iv is None or put_iv is None:
        return None
    return put_iv - call_iv


@register(
    name="term_structure",
    inputs=["CHAIN"],
    tier=Tier.T1,
    fidelity=Fidelity.EXACT,
    backtestable={"nse": True, "us": True},
    cost_ms=2,
)
def term_structure(ctx: FeatureContext) -> float | None:
    """Back-month ATM IV minus front-month ATM IV, using the nearest and
    farthest expiries present in `ctx.chain`. Positive = normal/contango
    (further-dated options priced with higher IV); negative = inverted/
    backwardation (near-term event risk priced in)."""
    if len(ctx.chain) < 2 or ctx.spot is None:
        return None
    front = min(ctx.chain, key=lambda s: s.expiry)
    back = max(ctx.chain, key=lambda s: s.expiry)
    if front.expiry == back.expiry:
        return None
    front_iv = _atm_iv(front.quotes, ctx.spot)
    back_iv = _atm_iv(back.quotes, ctx.spot)
    if front_iv is None or back_iv is None:
        return None
    return back_iv - front_iv


def _iv(q: Tick) -> float | None:
    """Prefer our own computed IV (`Tick.iv`) once it's wired up (see
    docs/PROGRESS.md P2 §8's known gap — `option_quote_row` currently
    never writes it), falling back to the vendor's own IV in the
    meantime. Written this way so nothing here needs to change again once
    that gap is fixed — it starts preferring the real column
    automatically the moment it's populated."""
    if q.iv is not None:
        return float(q.iv)
    if q.vendor_iv is not None:
        return float(q.vendor_iv)
    return None


def _iv_near_delta(quotes: list[Tick], option_type: str, target_delta: float) -> float | None:
    candidates = [q for q in quotes if q.option_type == option_type and q.delta is not None]
    if not candidates:
        return None
    nearest = min(candidates, key=lambda q: abs(float(q.delta) - target_delta))  # type: ignore[arg-type]
    return _iv(nearest)


def _atm_iv(quotes: list[Tick], spot: float) -> float | None:
    with_strike = [q for q in quotes if q.strike is not None]
    if not with_strike:
        return None
    nearest_strike = min((float(q.strike) for q in with_strike), key=lambda s: abs(s - spot))  # type: ignore[arg-type]
    ivs = [
        iv
        for q in with_strike
        if q.strike is not None and float(q.strike) == nearest_strike and (iv := _iv(q)) is not None
    ]
    if not ivs:
        return None
    return sum(ivs) / len(ivs)
