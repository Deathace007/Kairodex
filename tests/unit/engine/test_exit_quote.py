"""The book an exit is priced against.

Live 2026-08-10: every US exit — stop-losses included — was rejected
`NO_LIQUIDITY_AT_TOP_OF_BOOK` on every tick, because LSE publishes no
sizes and the fallback was 0. `compute_fill` fills
`floor(partial_fill_alpha * size)`, so any size below 4 fills nothing.
No US position had ever been exitable. See PROGRESS.md §15h.
"""

import datetime
from decimal import Decimal

from kairodex.core.enums import Segment
from kairodex.engine.orchestrator import _exit_quote

_TS = datetime.datetime(2026, 8, 5, 6, 0, tzinfo=datetime.UTC)


class _Q:
    """Stands in for an `OptionQuote` row — only the fields read here."""

    def __init__(self, bid=None, ask=None, bid_sz=None, ask_sz=None, volume=None, oi=None):
        self.bid, self.ask = bid, ask
        self.bid_sz, self.ask_sz = bid_sz, ask_sz
        self.volume, self.oi, self.ts = volume, oi, _TS


def test_us_quote_without_a_book_is_modelled_from_volume():
    """The live failure. LSE gives ltp and volume, nothing else — so the
    sizes have to come from the same proxy the entry path already uses,
    not from a 0 that can never fill."""
    quote = _exit_quote(Segment.US_STOCK, _Q(volume=2000), Decimal("2.50"))
    assert quote is not None
    assert quote.bid_sz >= 4 and quote.ask_sz >= 4  # enough for floor(0.25 * size) >= 1
    assert quote.bid < Decimal("2.50") < quote.ask


def test_us_exit_is_none_when_there_is_no_honest_book_to_model():
    """A contract too thin to model is reported, not invented — the caller
    logs EXIT_FAILED rather than fabricating liquidity."""
    assert _exit_quote(Segment.US_STOCK, _Q(volume=10), Decimal("2.50")) is None


def test_us_prefers_a_real_book_when_the_vendor_ever_publishes_one():
    observed = _Q(bid=Decimal("2.40"), ask=Decimal("2.60"), bid_sz=30, ask_sz=30)
    quote = _exit_quote(Segment.US_STOCK, observed, Decimal("2.50"))
    assert quote is not None
    assert (quote.bid, quote.ask, quote.bid_sz) == (Decimal("2.40"), Decimal("2.60"), 30)


def test_nse_is_never_modelled_even_when_sizes_are_missing():
    """§13e's rule: a real-book segment must not quietly move onto modelled
    prices because the feed hiccupped. NSE keeps the observed fallback,
    zero sizes and all — a loud unfillable exit beats a silent invented one."""
    quote = _exit_quote(Segment.NSE_STOCK, _Q(volume=5000), Decimal("2.50"))
    assert quote is not None
    assert quote.bid_sz == 0 and quote.ask_sz == 0
    assert quote.bid == Decimal("2.50") == quote.ask
