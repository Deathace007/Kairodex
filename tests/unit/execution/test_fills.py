import datetime
from decimal import Decimal

import pytest

from kairodex.core.enums import Side
from kairodex.execution.fills import compute_fill
from kairodex.execution.types import QuoteSnapshot

_NOW = datetime.datetime(2026, 8, 5, 10, 0, tzinfo=datetime.UTC)


def _quote(**overrides: object) -> QuoteSnapshot:
    base = dict(
        bid=Decimal(98),
        ask=Decimal(102),
        bid_sz=1000,
        ask_sz=1000,
        quote_ts=_NOW,
        oi=10000,
        chain_complete=True,
    )
    base.update(overrides)
    return QuoteSnapshot(**base)  # type: ignore[arg-type]


def test_buy_fill_hand_computed():
    """bid=98, ask=102 -> mid=100, half_spread=2, spread_bps=400.
    BUY: fill_price = 100 + 0.6*2 = 101.2. slippage_bps = 0.6*2/100*10000 = 120."""
    result = compute_fill(Side.BUY, 100, _quote(), _NOW)
    assert not result.rejected
    assert result.price == pytest.approx(Decimal("101.2"))
    assert result.spread_bps == pytest.approx(400.0)
    assert result.slippage_bps == pytest.approx(120.0)
    assert result.filled_qty == 100


def test_sell_fill_hand_computed():
    """SELL: fill_price = 100 - 0.6*2 = 98.8. slippage_bps = -120."""
    result = compute_fill(Side.SELL, 100, _quote(), _NOW)
    assert result.price == pytest.approx(Decimal("98.8"))
    assert result.slippage_bps == pytest.approx(-120.0)


def test_partial_fill_capped_at_alpha_times_top_of_book():
    """ask_sz=1000, alpha=0.25 -> max_fillable=250. Requesting 500 ->
    filled=250, a partial fill, not a rejection."""
    result = compute_fill(Side.BUY, 500, _quote(ask_sz=1000), _NOW)
    assert not result.rejected
    assert result.filled_qty == 250


def test_full_fill_when_request_under_top_of_book_cap():
    result = compute_fill(Side.BUY, 100, _quote(ask_sz=1000), _NOW)
    assert result.filled_qty == 100


def test_stale_quote_rejected():
    old_quote = _quote(quote_ts=_NOW - datetime.timedelta(seconds=3))
    result = compute_fill(Side.BUY, 10, old_quote, _NOW, max_quote_age_ms=2000)
    assert result.rejected
    assert result.reject_reason == "STALE_QUOTE"
    assert result.filled_qty == 0


def test_fresh_quote_within_max_age_passes():
    fresh_quote = _quote(quote_ts=_NOW - datetime.timedelta(seconds=1))
    result = compute_fill(Side.BUY, 10, fresh_quote, _NOW, max_quote_age_ms=2000)
    assert not result.rejected


def test_incomplete_chain_rejected():
    result = compute_fill(Side.BUY, 10, _quote(chain_complete=False), _NOW)
    assert result.rejected
    assert result.reject_reason == "INCOMPLETE_CHAIN"


def test_invalid_quote_crossed_book_rejected():
    result = compute_fill(Side.BUY, 10, _quote(bid=Decimal(105), ask=Decimal(100)), _NOW)
    assert result.rejected
    assert result.reject_reason == "INVALID_QUOTE"


def test_invalid_quote_zero_bid_rejected():
    result = compute_fill(Side.BUY, 10, _quote(bid=Decimal(0)), _NOW)
    assert result.rejected
    assert result.reject_reason == "INVALID_QUOTE"


def test_spread_too_wide_rejected():
    """bid=50, ask=150 -> mid=100, spread_bps=(100/100)*10000=10000, way
    over the default 500bps cap."""
    wide_quote = _quote(bid=Decimal(50), ask=Decimal(150))
    result = compute_fill(Side.BUY, 10, wide_quote, _NOW)
    assert result.rejected
    assert result.reject_reason == "SPREAD_TOO_WIDE"
    assert result.spread_bps == pytest.approx(10000.0)


def test_size_exceeds_max_pct_of_oi_rejected():
    """oi=100, max_pct_of_oi=0.10 -> max allowed = 10. Requesting 50 exceeds it."""
    result = compute_fill(Side.BUY, 50, _quote(oi=100), _NOW, max_pct_of_oi=0.10)
    assert result.rejected
    assert result.reject_reason == "SIZE_EXCEEDS_MAX_PCT_OF_OI"


def test_no_liquidity_at_top_of_book_rejected():
    result = compute_fill(Side.BUY, 10, _quote(ask_sz=0), _NOW)
    assert result.rejected
    assert result.reject_reason == "NO_LIQUIDITY_AT_TOP_OF_BOOK"


def test_zero_oi_does_not_trigger_pct_of_oi_rejection():
    """oi=0 (or unknown-but-zero) shouldn't divide-by-zero or spuriously
    reject — the OI check is skipped, not failed, when OI is 0."""
    result = compute_fill(Side.BUY, 10, _quote(oi=0), _NOW)
    assert not result.rejected


def test_none_oi_skips_the_oi_check():
    result = compute_fill(Side.BUY, 10, _quote(oi=None), _NOW)
    assert not result.rejected


def test_exit_tolerance_lets_a_real_stop_breach_fill_that_2s_rejected():
    """Regression, live 2026-08-07, using trade 2's own recorded book.

    HDFCBANK 750 C marked 8.70 against an 8.75 stop. The quote behind that
    mark was written at 08:50:10 and the engine tick ran at 08:50:25 — 15s
    later, because quotes come from a 60s REST poll, not a tick stream. The
    2s default rejected it STALE_QUOTE, the stop did not execute, and the
    position stayed open. Nothing about the book was wrong: 114bps spread
    against a 500bps limit, 7,800 on the bid, 23.9M OI."""
    quote = QuoteSnapshot(
        bid=Decimal("8.70"), ask=Decimal("8.80"), bid_sz=7800, ask_sz=5200,
        quote_ts=datetime.datetime(2026, 8, 7, 8, 50, 10, tzinfo=datetime.UTC),
        oi=23_923_900, chain_complete=True,
    )
    tick = datetime.datetime(2026, 8, 7, 8, 50, 25, tzinfo=datetime.UTC)

    stale = compute_fill(Side.SELL, 43, quote, tick, max_quote_age_ms=2_000)
    assert stale.rejected and stale.reject_reason == "STALE_QUOTE"

    filled = compute_fill(Side.SELL, 43, quote, tick, max_quote_age_ms=300_000)
    assert not filled.rejected
    assert filled.filled_qty == 43

    # Still bounded: a genuinely dead feed must not fabricate a fill.
    dead = datetime.datetime(2026, 8, 7, 9, 30, 0, tzinfo=datetime.UTC)  # ~40 min later
    assert compute_fill(Side.SELL, 43, quote, dead, max_quote_age_ms=300_000).rejected
