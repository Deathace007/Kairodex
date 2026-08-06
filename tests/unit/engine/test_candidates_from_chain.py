"""Candidate construction, and specifically the opt-in that decides whether
a segment may trade on a modelled book.

This is the exact seam where 15,084 US signals died (docs/PROGRESS.md §13c):
LSE publishes no bid/ask, so every US quote was skipped before the selector
ran. It is also the seam where getting the fix wrong would be worse than the
bug — quietly extending modelled pricing to NSE, which has a real book."""

import datetime
from decimal import Decimal

from kairodex.data.types import Tick
from kairodex.engine.orchestrator import _candidates_from_chain

_EXPIRY = datetime.date(2026, 8, 14)
_TS = datetime.datetime(2026, 8, 6, 14, 0, tzinfo=datetime.UTC)


def _tick(**over: object) -> Tick:
    base: dict[str, object] = {
        "instrument_key": "TEST",
        "ts": _TS,
        "strike": Decimal(100),
        "option_type": "C",
        "ltp": Decimal("5.00"),
        "volume": 1000,
    }
    base.update(over)
    return Tick(**base)  # type: ignore[arg-type]


def _quoted_tick() -> Tick:
    return _tick(bid=Decimal("4.90"), ask=Decimal("5.10"), bid_sz=50, ask_sz=50)


def test_real_book_is_used_verbatim_when_present():
    out = _candidates_from_chain([_quoted_tick()], _EXPIRY, synthetic_quotes=True)
    assert len(out) == 1
    # Even with synthesis enabled, an observed book always wins — modelling
    # over real data would discard the better information.
    assert out[0].bid == Decimal("4.90")
    assert out[0].ask == Decimal("5.10")


def test_missing_book_is_skipped_when_synthesis_is_off():
    """NSE's behaviour, unchanged: a leg with no bid/ask is dropped, not
    modelled. A feed hiccup must never silently switch a real-book segment
    onto assumed prices."""
    assert _candidates_from_chain([_tick()], _EXPIRY, synthetic_quotes=False) == []


def test_synthesis_is_off_by_default():
    """The safe default matters more than the convenient one: a new caller
    that forgets the flag gets NSE semantics, not fabricated quotes."""
    assert _candidates_from_chain([_tick()], _EXPIRY) == []


def test_missing_book_is_modelled_when_synthesis_is_on():
    out = _candidates_from_chain([_tick()], _EXPIRY, synthetic_quotes=True)
    assert len(out) == 1
    assert out[0].bid < Decimal("5.00") < out[0].ask
    assert out[0].bid_sz > 0


def test_synthesis_still_drops_a_contract_that_never_printed():
    """Enabling synthesis is not a promise that every leg becomes tradeable
    — no last price means no quote (execution.synthetic_quote)."""
    assert _candidates_from_chain([_tick(ltp=None)], _EXPIRY, synthetic_quotes=True) == []


def test_incomplete_legs_are_dropped_regardless():
    ticks = [_tick(strike=None), _tick(option_type=None)]
    assert _candidates_from_chain(ticks, _EXPIRY, synthetic_quotes=True) == []
