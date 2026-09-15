"""Pure helpers added by the 2026-09-15 fix plan."""

import datetime
from decimal import Decimal

from kairodex.core.enums import Side
from kairodex.data.types import Bar, ChainSnapshot
from kairodex.engine.orchestrator import (
    FORCED_EXIT_PENALTY_PCT,
    MANDATORY_EXITS,
    chain_at_min_dte,
    forced_exit_quote,
)
from kairodex.execution.fills import compute_fill
from kairodex.execution.types import QuoteSnapshot
from kairodex.features.loader import with_proxy_volume

_NOW = datetime.datetime(2026, 9, 15, 9, 50, tzinfo=datetime.UTC)


def test_forced_exit_quote_fills_whole_position_at_penalised_bid_despite_stale_quote():
    """Trade 196's shape: quote 3,470s old at the EOD exit. The real quote
    is refused; the forced one takes all 34 lots at bid x 0.95."""
    stale = QuoteSnapshot(
        bid=Decimal("6.10"), ask=Decimal("6.20"), bid_sz=1, ask_sz=1,
        quote_ts=_NOW - datetime.timedelta(seconds=3470),
    )
    assert compute_fill(Side.SELL, 34, stale, _NOW, max_quote_age_ms=300_000).reject_reason == (
        "STALE_QUOTE"
    )
    forced = compute_fill(
        Side.SELL, 34, forced_exit_quote(stale, qty=34, now=_NOW), _NOW, max_quote_age_ms=300_000
    )
    assert not forced.rejected
    assert forced.filled_qty == 34
    assert forced.price == Decimal("6.10") * Decimal(str(1 - FORCED_EXIT_PENALTY_PCT))


def test_only_clock_driven_exits_may_be_forced():
    assert {"EOD_EXIT", "OVERNIGHT_EXIT", "EXPIRY_EXIT"} == MANDATORY_EXITS
    assert "STOP_LOSS" not in MANDATORY_EXITS


def test_chain_at_min_dte_drops_this_weeks_expiry():
    today = datetime.date(2026, 9, 15)
    snaps = [
        ChainSnapshot(underlying="NIFTY", expiry=datetime.date(2026, 9, 15), ts=_NOW),
        ChainSnapshot(underlying="NIFTY", expiry=datetime.date(2026, 9, 22), ts=_NOW),
    ]
    assert [s.expiry.day for s in chain_at_min_dte(snaps, today, 7)] == [22]
    assert len(chain_at_min_dte(snaps, today, 0)) == 2


def _bar(minute: int, volume: int) -> Bar:
    c = Decimal(100)
    return Bar(ts=_NOW + datetime.timedelta(minutes=minute), open=c, high=c, low=c, close=c,
               volume=volume)


def test_with_proxy_volume_takes_future_volume_by_minute_and_keeps_prices():
    index = [_bar(0, 0), _bar(1, 0), _bar(2, 0)]
    future = [_bar(0, 500), _bar(2, 900)]
    merged = with_proxy_volume(index, future)
    assert [b.volume for b in merged] == [500, 0, 900]
    assert [b.close for b in merged] == [b.close for b in index]
    assert with_proxy_volume(index, []) == index
