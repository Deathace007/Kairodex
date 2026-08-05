"""Fill model (ARCHITECTURE.md §12) — a pure function of (order, quote,
now), no state, no I/O. `SimulatedBroker` (simulator.py) is where the
stateful parts (latency, partial-fill re-attempts, attempt expiry) live;
this file is just "given a quote right now, what would happen."
"""

from __future__ import annotations

import datetime
import math
from decimal import Decimal

from kairodex.core.enums import Side
from kairodex.execution.types import FillOutcome, QuoteSnapshot

_DEFAULT_MAX_QUOTE_AGE_MS = 2000
_DEFAULT_K = 0.6  # fill = mid +/- k*half_spread; 0 = mid, 1 = full spread
_DEFAULT_PARTIAL_FILL_ALPHA = 0.25  # cap: fill at most this fraction of top-of-book size
_DEFAULT_MAX_SPREAD_BPS = 500.0
_DEFAULT_MAX_PCT_OF_OI = 0.10


def compute_fill(
    side: Side,
    requested_qty: int,
    quote: QuoteSnapshot,
    now: datetime.datetime,
    *,
    max_quote_age_ms: int = _DEFAULT_MAX_QUOTE_AGE_MS,
    k: float = _DEFAULT_K,
    partial_fill_alpha: float = _DEFAULT_PARTIAL_FILL_ALPHA,
    max_spread_bps: float = _DEFAULT_MAX_SPREAD_BPS,
    max_pct_of_oi: float = _DEFAULT_MAX_PCT_OF_OI,
) -> FillOutcome:
    """Marketable orders only (§12 v1 — no passive/queue-position model).
    Rejects before pricing anything: a stale quote, an incomplete chain,
    or a crossed/zero book. Then rejects on policy: spread too wide, or
    size too large relative to open interest. Only then computes a fill
    price and (possibly partial) quantity."""
    age_ms = (now - quote.quote_ts).total_seconds() * 1000
    if age_ms > max_quote_age_ms:
        return FillOutcome(0, None, None, None, True, "STALE_QUOTE")
    if not quote.chain_complete:
        return FillOutcome(0, None, None, None, True, "INCOMPLETE_CHAIN")
    if quote.bid <= 0 or quote.ask <= 0 or quote.ask < quote.bid:
        return FillOutcome(0, None, None, None, True, "INVALID_QUOTE")

    mid = (quote.bid + quote.ask) / 2
    half_spread = (quote.ask - quote.bid) / 2
    spread_bps = float((quote.ask - quote.bid) / mid) * 10000 if mid > 0 else None

    if spread_bps is not None and spread_bps > max_spread_bps:
        return FillOutcome(0, None, spread_bps, None, True, "SPREAD_TOO_WIDE")
    if quote.oi is not None and quote.oi > 0 and requested_qty > max_pct_of_oi * quote.oi:
        return FillOutcome(0, None, spread_bps, None, True, "SIZE_EXCEEDS_MAX_PCT_OF_OI")

    # Worse than mid, better than the full spread — buying lifts toward the
    # ask, selling presses toward the bid.
    direction_sign = Decimal(1) if side is Side.BUY else Decimal(-1)
    fill_price = mid + direction_sign * Decimal(str(k)) * half_spread
    slippage_bps = (
        float(direction_sign * Decimal(str(k)) * half_spread / mid) * 10000 if mid > 0 else None
    )

    top_of_book_size = quote.ask_sz if side is Side.BUY else quote.bid_sz
    max_fillable = math.floor(partial_fill_alpha * top_of_book_size)
    filled_qty = min(requested_qty, max(max_fillable, 0))
    if filled_qty <= 0:
        return FillOutcome(0, None, spread_bps, slippage_bps, True, "NO_LIQUIDITY_AT_TOP_OF_BOOK")

    return FillOutcome(filled_qty, fill_price, spread_bps, slippage_bps, False, None)
