"""End-to-end (still synthetic, DB-free): ReferenceStrategy.evaluate feeds
ConfluenceScorer, proving the whole entry-side pipeline wires together,
not just each piece in isolation."""

import datetime
from decimal import Decimal

from kairodex.core.enums import Segment, Side
from kairodex.data.types import Bar
from kairodex.features.types import FeatureContext
from kairodex.strategy.protocol import ReferenceStrategy
from kairodex.strategy.scorer import ConfluenceScorer
from kairodex.strategy.types import MarketContext

_T0 = datetime.datetime(2026, 8, 5, 9, 15, tzinfo=datetime.UTC)


def _bar(minutes: int, close: float) -> Bar:
    c = Decimal(str(close))
    ts = _T0 + datetime.timedelta(minutes=minutes)
    return Bar(ts=ts, open=c, high=c, low=c, close=c, volume=1000)


def test_strongly_bullish_features_produce_a_buy_signal():
    bars = [_bar(0, 100.0), _bar(1, 102.0)]  # +2% move
    features = {
        "trend_state_strength": 0.10,  # strong uptrend
        "oi_change": 0.20,  # buildup, agrees with the up-move -> full conviction
        "iv_skew": -8.0,  # calls richer than puts -> bullish
        "relative_strength_vs_index": 0.05,  # outperforming
    }
    ctx = MarketContext(
        feature_ctx=FeatureContext(as_of=_T0, segment=Segment.NSE_INDEX, underlying_bars=bars),
        features=features,
    )
    strategy = ReferenceStrategy()
    evidence = strategy.evaluate(ctx)
    assert len(evidence) == 4  # every detector fired

    result = ConfluenceScorer().score(evidence)
    assert result.direction is Side.BUY
    assert len(result.agreeing_families) == 4
    assert result.confidence > 0.5


def test_conflicting_features_produce_no_signal():
    bars = [_bar(0, 100.0), _bar(1, 101.0)]
    features = {
        "trend_state_strength": 0.10,  # bullish
        "oi_change": -0.20,  # covering, weak bullish
        "iv_skew": 8.0,  # bearish
        "relative_strength_vs_index": -0.05,  # bearish
    }
    ctx = MarketContext(
        feature_ctx=FeatureContext(as_of=_T0, segment=Segment.NSE_INDEX, underlying_bars=bars),
        features=features,
    )
    result = ConfluenceScorer().score(ReferenceStrategy().evaluate(ctx))
    assert result.direction is None


def test_missing_features_yield_fewer_evidence_items_not_a_crash():
    ctx = MarketContext(
        feature_ctx=FeatureContext(as_of=_T0, segment=Segment.NSE_INDEX, underlying_bars=[]),
        features={},
    )
    evidence = ReferenceStrategy().evaluate(ctx)
    assert evidence == []
    result = ConfluenceScorer().score(evidence)
    assert result.direction is None
