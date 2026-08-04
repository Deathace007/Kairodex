"""Decode tests against synthetic FeedResponse messages built from the real
generated protobuf classes — no live credentials or market hours needed.
Pins the wire-format -> Tick mapping in kairodex.data.upstox.feed so a
future regeneration of the .proto can't silently change field semantics."""

import datetime
from decimal import Decimal

from kairodex.data.upstox.feed import _parse_feed
from kairodex.data.upstox.proto import MarketDataFeedV3_pb2 as pb

_TS = datetime.datetime(2026, 8, 4, 10, 0, 0, tzinfo=datetime.UTC)


def test_ltpc_only_feed():
    feed = pb.Feed(ltpc=pb.LTPC(ltp=101.5, ltt=1, ltq=10, cp=100.0))
    tick = _parse_feed("NSE_INDEX|Nifty 50", feed, _TS)
    assert tick is not None
    assert tick.ltp == Decimal("101.5")
    assert tick.bid is None


def test_market_full_feed_option_leg():
    full = pb.MarketFullFeed(
        ltpc=pb.LTPC(ltp=52.5),
        marketLevel=pb.MarketLevel(bidAskQuote=[pb.Quote(bidQ=75, bidP=52.0, askQ=75, askP=53.0)]),
        optionGreeks=pb.OptionGreeks(delta=0.45, theta=-1.2, gamma=0.002, vega=0.15, rho=0.01),
        vtt=12000,
        oi=45000,
        iv=18.5,
    )
    feed = pb.Feed(fullFeed=pb.FullFeed(marketFF=full))
    tick = _parse_feed("NSE_FO|98917", feed, _TS)
    assert tick is not None
    assert tick.ltp == Decimal("52.5")
    assert tick.bid == Decimal("52.0")
    assert tick.ask == Decimal("53.0")
    assert tick.bid_sz == 75
    assert tick.volume == 12000
    assert tick.oi == 45000
    assert tick.delta == Decimal("0.45")


def test_index_full_feed_has_no_greeks_or_depth():
    feed = pb.Feed(fullFeed=pb.FullFeed(indexFF=pb.IndexFullFeed(ltpc=pb.LTPC(ltp=24800.0))))
    tick = _parse_feed("NSE_INDEX|Nifty 50", feed, _TS)
    assert tick is not None
    assert tick.ltp == Decimal("24800")
    assert tick.bid is None
    assert tick.delta is None


def test_first_level_with_greeks_feed():
    first = pb.FirstLevelWithGreeks(
        ltpc=pb.LTPC(ltp=10.0),
        firstDepth=pb.Quote(bidQ=5, bidP=9.8, askQ=5, askP=10.2),
        optionGreeks=pb.OptionGreeks(delta=0.3),
        vtt=100,
        oi=500,
        iv=20.0,
    )
    feed = pb.Feed(firstLevelWithGreeks=first)
    tick = _parse_feed("NSE_FO|1", feed, _TS)
    assert tick is not None
    assert tick.bid == Decimal("9.8")
    assert tick.delta == Decimal("0.3")


def test_unset_zero_fields_read_as_none_not_zero():
    # proto3 has no field presence for these scalars — 0.0 must not be
    # mistaken for a real quote of zero.
    feed = pb.Feed(ltpc=pb.LTPC(ltp=0.0))
    tick = _parse_feed("X", feed, _TS)
    assert tick is not None
    assert tick.ltp is None
