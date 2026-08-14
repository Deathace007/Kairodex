"""Contract selector (ARCHITECTURE.md §10's pipeline step between
confluence and sizing; §11's affordability constraint: "the contract
selector treats it as a constraint rather than a preference. In practice
it biases NSE toward slightly OTM strikes"). Turns a directional signal
into one actual option leg.

This is an options-*buying* platform — `direction` here is the same
overloaded `Side` as everywhere else (see `core.enums.Side`'s docstring):
BUY = bullish bias -> buy a call, SELL = bearish bias -> buy a put. The
resulting order is always `Side.BUY` (opening a long option), never a
sale/write.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from decimal import Decimal

from kairodex.core.enums import Side

_DEFAULT_MIN_DTE = 1
_DEFAULT_MAX_DTE = 45
# 0.40 -> 0.50 on 2026-08-14. This constant stood as an untested "slightly
# OTM is conventional" choice with a note to recalibrate once something
# could measure it. It sits directly inside the trade's breakeven
# equation, so it was never a preference:
#
#     required underlying move = (spread + theta_over_hold) / (delta * spot)
#
# Both the numerator and `delta` move with strike, so the function has an
# interior minimum. Measured over 778k liquid nse_stock chain quotes
# (OI > 500k) across 5 sessions, expressed in ATR of the underlying so it
# is comparable with `signals.forward_outcome` (median ATR 0.0689% of
# price), charging theta over the 121-minute median hold:
#
#   |delta|      spread   theta   TOTAL hurdle
#   0.01-0.15     1.799   1.831   3.721 ATR
#   0.15-0.25     0.727   1.018   1.843
#   0.25-0.35     0.604   0.797   1.486
#   0.35-0.45     0.554   0.635   1.276   <- the old target
#   0.45-0.55     0.512   0.536   1.125   <- here
#   0.55-0.65     0.570   0.440   1.068   <- aggregate minimum
#   0.65-0.75     0.722   0.355   1.095
#   0.75-1.00     1.786   0.166   1.947
#
# A clean U. Far OTM is destroyed by relative spread (10.5% at 0.03
# delta — the "cheap lottery ticket" is the most expensive thing on the
# board); far ITM by lost leverage. Per session the 0.45-0.55 band beats
# 0.35-0.45 in ALL FIVE (1.210/1.122, 1.318/1.082, 1.259/1.154,
# 1.284/1.152, 1.313/1.135), so this is not one day's artefact.
#
# NOT moved to the 0.55-0.65 aggregate minimum, deliberately. The basin
# is flat (1.068 vs 1.125, a 0.06 ATR difference) and going deeper ITM
# spends two things this strategy is built on: gamma — the convexity
# that PROGRESS.md §19d's whole ladder argument rests on — and premium
# per lot, which binds against `max_premium_pct` and buys fewer lots.
# 0.50 takes 0.15 of the 0.21 ATR available and keeps both.
#
# What this does NOT do is create edge: it moves the share of signals
# whose best moment can cover the option from 42.7% to 46.0% (improving
# in all 6 sessions). It lowers the cost of being right; it does not
# make the system right.
_DEFAULT_TARGET_DELTA = 0.50

# How far from target_delta the "closest available" candidate is still
# allowed to be. Without this, `min(affordable, key=distance)` always
# returns *something* — even the single closest of a pool that's nowhere
# near tradeable. Found live 2026-08-06: on a thin US vendor, the only
# candidates with enough recorded volume to synthesize a fillable book
# (execution.synthetic_quote) were routinely far-OTM (delta ~0.005, a
# lottery ticket) or far-ITM (delta ~0.95, a leveraged stock substitute at
# a huge premium) — "closest of a bad set" is still a bad pick, and the
# user's own standing instruction is to trade only high-conviction setups,
# not whatever is technically fillable. 0.25 keeps calls roughly in a
# 0.15-0.65 delta band: same documented-assumption status as
# _DEFAULT_TARGET_DELTA itself — a first-pass judgment call, not derived
# or backtested, and the user's explicit choice over leaving this
# unbounded. Applied identically to the no-delta moneyness fallback below
# even though the two are on different scales (delta vs normalized strike
# distance) — that path is rare (this vendor supplies delta on nearly
# every leg) and not worth a second, separately-tuned threshold yet.
_DEFAULT_MAX_DELTA_DISTANCE = 0.25

# Reject a leg whose bid-ask spread is wider than this fraction of its mid.
#
# Added 2026-08-14. Until then the only microstructure filter here was
# `bid > 0 and ask > 0 and ask >= bid` — a contract was tradeable if it was
# quoted at all, at any width. Measured over the 34 NSE trades closed since
# the 08-11 reset, by entry premium:
#
#   premium        n   avg round-trip spread   net P&L
#   under Rs 5     8            4.39%          -Rs 2,619
#   Rs 5-20       13            1.79%          -Rs 3,829
#   Rs 20-60       9            2.09%          +Rs 2,627
#   Rs 60+ (idx)   4            0.50%          -Rs 1,616
#
# Relative spread is essentially 1/premium on NSE stock options, so the
# cheap end is where the damage is. The worst single case: ITC 275 P bought
# at Rs 2.18 with a 9.53% round-trip spread against a best-ever excursion of
# +3.2% — that position could not have been closed at a profit at any
# moment of its life. It was not a bad trade, it was an untradeable
# contract. Thirteen of the 34 had a round-trip spread at or above their own
# MFE.
#
# 0.02 is one-way, so ~4% round trip. It keeps the Rs 20-60 band (the only
# profitable one) and the index legs, and removes the sub-Rs-5 band. Chosen
# to sit just above the 20-60 band's observed 2.09% round trip rather than
# fitted: the measurement says "the cheap tail is fatal", not "the optimum
# is exactly here".
#
# NOTE this is knowable *before* entry, which is what makes it actionable —
# unlike the MFE-vs-spread comparison above, which is partly circular (a
# trade that never rose is a trade that lost).
#
# CONSEQUENCE FOR US, observed live within seconds of deploying this and
# recorded so it is not re-discovered as a mystery: both US segments now
# reject EVERY candidate with `NO_CONTRACT_INSIDE_SPREAD_LIMIT`, 100% of the
# time, and this is deterministic rather than distributional.
# `execution.synthetic_quote.SPREAD_PCT` is 0.04 — LSE publishes zero bid,
# ask and size on every row (§15i), so the entire US book is *fabricated* at
# an assumed 4% spread, which can never clear a 2% limit.
#
# Left as-is deliberately. The threshold was measured on real NSE quotes and
# means nothing applied to a synthetic book; refusing to buy an instrument
# whose spread is an assumption is the correct behaviour, not a bug. It is a
# THIRD independent reason US cannot trade, alongside §15i's missing order
# book and §19a's unreachable `us_index` confidence gate — and like that
# one, it is a gate sitting outside its own distribution, so it must be
# re-sited when the data layer is fixed. **Do not unhalt US expecting trades
# until `max_relative_spread` is given a real per-segment value.**
_DEFAULT_MAX_RELATIVE_SPREAD = 0.02


def _relative_spread(c: ContractCandidate) -> float:
    """(ask - bid) / mid. Callers have already established bid/ask > 0."""
    mid = (c.bid + c.ask) / 2
    if mid <= 0:  # pragma: no cover — guarded by the bid/ask > 0 filter
        return float("inf")
    return float((c.ask - c.bid) / mid)


@dataclass(frozen=True, slots=True)
class ContractCandidate:
    instrument_id: int
    strike: Decimal
    option_type: str  # "C" | "P"
    expiry: datetime.date
    bid: Decimal
    ask: Decimal
    delta: Decimal | None = None
    # Only needed downstream (building a QuoteSnapshot for the execution
    # simulator once this candidate is selected) — optional so selection
    # logic/tests never had to care about them.
    bid_sz: int | None = None
    ask_sz: int | None = None
    oi: int | None = None
    quote_ts: datetime.datetime | None = None


@dataclass(frozen=True, slots=True)
class SelectionResult:
    selected: ContractCandidate | None
    reason: str | None = None  # populated when selected is None

    @property
    def mid_price(self) -> Decimal | None:
        if self.selected is None:
            return None
        return (self.selected.bid + self.selected.ask) / 2


def select_contract(
    candidates: list[ContractCandidate],
    direction: Side,
    *,
    spot: Decimal,
    equity: Decimal,
    max_premium_pct: float,
    lot_size: int,
    as_of: datetime.date,
    min_dte: int = _DEFAULT_MIN_DTE,
    max_dte: int = _DEFAULT_MAX_DTE,
    target_delta: float = _DEFAULT_TARGET_DELTA,
    max_delta_distance: float = _DEFAULT_MAX_DELTA_DISTANCE,
    max_relative_spread: float = _DEFAULT_MAX_RELATIVE_SPREAD,
) -> SelectionResult:
    option_type = "C" if direction is Side.BUY else "P"
    in_window = [
        c
        for c in candidates
        if c.option_type == option_type
        and c.bid > 0
        and c.ask > 0
        and c.ask >= c.bid
        and min_dte <= (c.expiry - as_of).days <= max_dte
    ]
    if not in_window:
        return SelectionResult(None, "NO_CANDIDATES_IN_EXPIRY_WINDOW")

    # Relative spread, applied here rather than as a risk gate on purpose:
    # a gate rejects the whole *signal*, while this only rejects one leg and
    # lets a better strike on the same underlying still be chosen. Selection
    # is where "which contract" belongs.
    tradeable = [c for c in in_window if _relative_spread(c) <= max_relative_spread]
    if not tradeable:
        return SelectionResult(None, "NO_CONTRACT_INSIDE_SPREAD_LIMIT")

    max_premium = Decimal(str(max_premium_pct)) * equity
    affordable = [c for c in tradeable if ((c.bid + c.ask) / 2) * lot_size <= max_premium]
    if not affordable:
        return SelectionResult(None, "NO_AFFORDABLE_CONTRACT")

    # Puts carry negative delta by this codebase's own convention (see
    # kairodex.features.compute.iv._iv_near_delta's `-target_delta` call for
    # puts) — comparing a put's signed delta against a bare positive
    # `target_delta` always made the *weakest* (near-zero-delta) put look
    # closest to target, silently breaking delta-targeting for every
    # bearish trade. Sign the target to match `option_type` instead.
    signed_target_delta = target_delta if option_type == "C" else -target_delta

    def distance(c: ContractCandidate) -> float:
        if c.delta is not None:
            return abs(float(c.delta) - signed_target_delta)
        # No vendor delta on this leg — fall back to normalized distance
        # from spot as a moneyness proxy.
        return abs(float(c.strike - spot)) / float(spot) if spot > 0 else float("inf")

    selected = min(affordable, key=distance)
    if distance(selected) > max_delta_distance:
        return SelectionResult(None, "NO_CONTRACT_NEAR_TARGET_DELTA")
    return SelectionResult(selected)
