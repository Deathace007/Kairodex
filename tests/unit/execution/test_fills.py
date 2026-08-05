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
