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


_FLOW_SPAN = 30  # oi_price_flow measures price over its own 30m OI window


def test_strongly_bullish_features_produce_a_buy_signal():
    bars = [_bar(0, 100.0), _bar(_FLOW_SPAN, 102.0)]  # +2% over the flow window
    features = {
        "trend_state_strength": 0.10,  # strong uptrend
        "oi_change": 0.20,  # buildup, agrees with the up-move -> full conviction
        # `iv_skew` is deliberately still supplied. The VOLATILITY detector
        # was unwired on 2026-08-14 (see protocol._REFERENCE_DETECTORS), and
        # leaving the feature in the dict pins the thing that matters: the
        # strategy no longer reads it. If it is ever re-wired, this test
        # starts reporting 4 and says so.
        "iv_skew": -8.0,
        "relative_strength_vs_index": 0.05,  # outperforming
    }
    ctx = MarketContext(
        feature_ctx=FeatureContext(as_of=_T0, segment=Segment.NSE_INDEX, underlying_bars=bars),
        features=features,
    )
    strategy = ReferenceStrategy()
    evidence = strategy.evaluate(ctx)
    assert len(evidence) == 3  # structure + flow + relative-strength
    assert {e.family.value for e in evidence} == {"structure", "flow", "relative_strength"}

    result = ConfluenceScorer().score(evidence)
    assert result.direction is Side.BUY
    assert len(result.agreeing_families) == 3
    assert result.confidence > 0.5


def test_conflicting_features_produce_no_signal():
    """With three families and `min_families=2`, abstention is the case
    where no side reaches two — one bullish, one bearish, one with no
    real opinion. The pre-2026-08-14 version of this test relied on the
    VOLATILITY family to supply the second bearish vote; without it, the
    same feature values would resolve 2-1 to BUY rather than abstain, so
    the conflict is now built from the families that actually vote."""
    # +0.05% over the flow window: real, but below the scorer's own 0.20
    # agreement threshold once scaled, so FLOW holds no opinion.
    bars = [_bar(0, 100.0), _bar(_FLOW_SPAN, 100.05)]
    features = {
        "trend_state_strength": 0.10,  # bullish
        "oi_change": 0.20,  # buildup, but the price leg is ~flat
        "relative_strength_vs_index": -0.05,  # bearish
    }
    ctx = MarketContext(
        feature_ctx=FeatureContext(as_of=_T0, segment=Segment.NSE_INDEX, underlying_bars=bars),
        features=features,
    )
    evidence = ReferenceStrategy().evaluate(ctx)
    assert len(evidence) == 3  # all three fired — they just do not agree
    result = ConfluenceScorer().score(evidence)
    assert result.direction is None
    assert result.confidence == 0.0


def test_missing_features_yield_fewer_evidence_items_not_a_crash():
    ctx = MarketContext(
        feature_ctx=FeatureContext(as_of=_T0, segment=Segment.NSE_INDEX, underlying_bars=[]),
        features={},
    )
    evidence = ReferenceStrategy().evaluate(ctx)
    assert evidence == []
    result = ConfluenceScorer().score(evidence)
    assert result.direction is None
