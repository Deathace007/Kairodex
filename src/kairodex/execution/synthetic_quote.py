"""Modelled two-sided quote for vendors that publish trade prints but no
order book.

LSE — the only US options source (ADR 0007) — carries no bid/ask/sizes on
either its REST chain or its stream. Confirmed live 2026-08-06 against an
open US session: 400 consecutive option ticks, every one `bid=None,
ask=None`. Its `Tick` type declares the fields; they are never populated.
What it does deliver is real: last traded price, today's volume, IV, full
greeks, underlying price. So US options are *time and sales*, not quotes.

Everything downstream of contract selection assumes a book:
`ContractCandidate.bid/.ask` are non-optional and `execution.fills` prices
every fill off `mid +/- k*half_spread`. Both were written against Upstox,
which publishes real depth. The result was that 100% of US signals — 15,084
of them — died at `NO_CANDIDATES_IN_EXPIRY_WINDOW` before reaching a single
risk gate (docs/PROGRESS.md §13c).

**This module fabricates a book. Three rules keep that honest:**

1. *Recorded data is never touched.* `option_quotes.bid/ask` stay NULL, and
   faithfully so — what the vendor said is what we store, permanently. The
   model applies only in the live trading path, at candidate construction.
2. *It errs expensive.* An assumed spread that is too tight silently
   flatters every US entry and exit, which is the one failure mode a
   research system cannot tolerate — it would manufacture edge that does not
   exist. The default is pessimistic against real liquid US spreads (~1-2%
   of premium); being wrong toward "trading cost more than modelled" only
   ever understates results.
3. *It is labelled.* Every fill priced this way is marked synthetic all the
   way onto the trade record (`context_entry.synthetic_quote`), so no
   analysis can mistake a modelled fill for an observed one.

`SPREAD_PCT` is a first-pass assumption, NOT a measurement — with no bid/ask
from this vendor there is nothing here to calibrate against, and a
same-underlying NSE comparison would not transfer. Treat every US fill price
as carrying that uncertainty. It is deliberately not per-segment YAML config
yet: there is no evidence with which to differentiate one segment from
another, and a knob implies a calibration that does not exist.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

# Total spread as a fraction of premium (so half this much on each side of
# last price). 4% sits well above typical liquid US option spreads and below
# execution.fills' own 500bps SPREAD_TOO_WIDE policy limit — a synthetic
# quote must not be so wide that it trips a rejection meant for genuinely
# illiquid books, nor so tight that it invents a fill nobody could get.
# ponytail: flat rate. The obvious upgrade is widening it as volume/OI fall
# (cheap and thin contracts really do trade wider), which is worth doing the
# moment there is any realized-fill evidence to fit against — not before.
SPREAD_PCT = Decimal("0.04")

# US options quote in $0.01 increments below $3 and $0.05 above (the penny
# pilot convention). A modelled spread narrower than the exchange's own tick
# is not a spread.
_PENNY_TICK_THRESHOLD = Decimal("3")
_TICK_BELOW = Decimal("0.01")
_TICK_ABOVE = Decimal("0.05")

# Top-of-book size proxy, from today's volume. Displayed size and daily
# volume are only loosely coupled in reality — a market maker shows tens of
# contracts on a liquid option almost regardless of how many trade — so this
# scales sub-linearly and caps out rather than pretending a busy contract
# shows a proportionally enormous book.
#
# This is the loosest assumption in this module and the one most worth
# revisiting first. Measured against real recorded US legs (2026-08-06):
# volume is far thinner than the "chain full of liquid options" intuition
# suggests — p99 across all legs is ~102/day and the single busiest contract
# in the whole watchlist did 3,628. So the divisor materially decides how
# much of the chain is tradeable at all, and a too-generous one would
# manufacture fills on contracts nobody could actually get filled in.
_VOLUME_TO_TOP_OF_BOOK = 20
_MAX_TOP_OF_BOOK = 50

# execution.fills fills at most `partial_fill_alpha` (0.25) of top-of-book,
# floored — so a modelled size below 4 cannot fill even one lot, and the
# contract would be selected and then rejected NO_LIQUIDITY_AT_TOP_OF_BOOK
# every time. Dropping it here instead makes thin contracts fail to be
# *candidates* rather than fail at execution, which is both cheaper and
# more honest: the selector then picks the best contract it can actually
# trade, rather than picking on delta alone and discovering afterwards that
# nobody is quoting it. That ordering is also what "only high-conviction
# setups" requires — an untradeable strike is not a setup.
_MIN_TOP_OF_BOOK = 4


@dataclass(frozen=True, slots=True)
class SyntheticQuote:
    bid: Decimal
    ask: Decimal
    bid_sz: int
    ask_sz: int


def synthesize_quote(
    ltp: Decimal | None,
    volume: int | None,
    *,
    spread_pct: Decimal = SPREAD_PCT,
) -> SyntheticQuote | None:
    """Build a modelled book around `ltp`, or None when there is nothing
    honest to build one from.

    Returns None — rather than a zero-width or unfillable quote — in three
    cases, so the caller drops the contract instead of trading a fabricated
    one:

      * no last price: the contract has never printed, so there is no
        evidence it can be bought at any price;
      * sub-tick premium, where a book cannot straddle zero;
      * too little volume to model a fillable top of book (see
        `_MIN_TOP_OF_BOOK`) — a strike nobody is trading is not a setup.
    """
    if ltp is None or ltp <= 0:
        return None

    tick = _TICK_BELOW if ltp < _PENNY_TICK_THRESHOLD else _TICK_ABOVE
    half_spread = max(ltp * spread_pct / 2, tick / 2)

    bid = ltp - half_spread
    if bid <= 0:
        return None

    size = min((volume or 0) // _VOLUME_TO_TOP_OF_BOOK, _MAX_TOP_OF_BOOK)
    if size < _MIN_TOP_OF_BOOK:
        return None

    return SyntheticQuote(bid=bid, ask=ltp + half_spread, bid_sz=size, ask_sz=size)
