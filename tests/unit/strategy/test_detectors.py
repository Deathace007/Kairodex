import datetime
import math
from decimal import Decimal

import pytest

from kairodex.core.enums import Segment
from kairodex.data.types import Bar
from kairodex.features.types import FeatureContext
from kairodex.strategy.detectors.flow import oi_price_flow_detector
from kairodex.strategy.detectors.relative_strength import relative_strength_detector
from kairodex.strategy.detectors.structure import trend_structure_detector
from kairodex.strategy.detectors.volatility import iv_skew_detector
from kairodex.strategy.scorer import _DEFAULT_AGREEMENT_THRESHOLD
from kairodex.strategy.types import DetectorFamily, MarketContext

_T0 = datetime.datetime(2026, 8, 5, 9, 15, tzinfo=datetime.UTC)


def _bar(minutes: int, close: float) -> Bar:
    c = Decimal(str(close))
    ts = _T0 + datetime.timedelta(minutes=minutes)
    return Bar(ts=ts, open=c, high=c, low=c, close=c, volume=1000)


def _ctx(features: dict[str, float], bars: list[Bar] | None = None) -> MarketContext:
    feature_ctx = FeatureContext(
        as_of=_T0, segment=Segment.NSE_INDEX, underlying_bars=bars or [_bar(0, 100.0)]
    )
    return MarketContext(feature_ctx=feature_ctx, features=features)


def test_trend_structure_hand_computed():
    """trend_state_strength=0.0018, scale=0.0018 -> tanh(1) = 0.7615942."""
    evidence = trend_structure_detector(_ctx({"trend_state_strength": 0.0018}))
    assert evidence is not None
    assert evidence.family == DetectorFamily.STRUCTURE
    assert evidence.score == pytest.approx(math.tanh(1.0), abs=1e-9)


def test_trend_structure_reads_a_real_live_value_as_a_real_opinion():
    """Regression for §16: the 0.05 scale made the whole live distribution
    unusable. |trend_state_strength| p50 over 20,399 real NSE signals was
    0.0006 — which scored 0.012, indistinguishable from no signal, yet still
    cast a full confluence vote. It must now clear the scorer's 0.20 floor."""
    evidence = trend_structure_detector(_ctx({"trend_state_strength": 0.0006}))
    assert evidence is not None
    assert evidence.score > _DEFAULT_AGREEMENT_THRESHOLD


def test_trend_structure_negative_is_bearish():
    evidence = trend_structure_detector(_ctx({"trend_state_strength": -0.05}))
    assert evidence is not None
    assert evidence.score < 0


def test_trend_structure_none_without_feature():
    assert trend_structure_detector(_ctx({})) is None


def test_relative_strength_hand_computed():
    """relative_strength_vs_index=0.03, scale=0.03 -> tanh(1) = 0.7615942."""
    evidence = relative_strength_detector(_ctx({"relative_strength_vs_index": 0.03}))
    assert evidence is not None
    assert evidence.family == DetectorFamily.RELATIVE_STRENGTH
    assert evidence.score == pytest.approx(math.tanh(1.0), abs=1e-9)


def test_iv_skew_positive_skew_is_bearish():
    """Positive skew (puts richer than calls) -> bearish -> negative score.
    iv_skew=5.0, scale=5.0 -> score = tanh(-1) = -0.7615942."""
    evidence = iv_skew_detector(_ctx({"iv_skew": 5.0}))
    assert evidence is not None
    assert evidence.family == DetectorFamily.VOLATILITY
    assert evidence.score == pytest.approx(math.tanh(-1.0), abs=1e-9)


def test_iv_skew_negative_skew_is_bullish():
    evidence = iv_skew_detector(_ctx({"iv_skew": -5.0}))
    assert evidence is not None
    assert evidence.score == pytest.approx(math.tanh(1.0), abs=1e-9)


def test_flow_long_buildup_full_conviction():
    """Price 100 -> 101 (+1%), OI up -> long buildup, full conviction.
    score = tanh(0.01/0.01) * 1.0 = tanh(1) = 0.7615942."""
    bars = [_bar(0, 100.0), _bar(1, 101.0)]
    evidence = oi_price_flow_detector(_ctx({"oi_change": 0.10}, bars=bars))
    assert evidence is not None
    assert evidence.family == DetectorFamily.FLOW
    assert evidence.score == pytest.approx(math.tanh(1.0), abs=1e-9)


def test_flow_short_covering_half_conviction():
    """Price up but OI down -> short covering, half conviction.
    score = tanh(1) * 0.5."""
    bars = [_bar(0, 100.0), _bar(1, 101.0)]
    evidence = oi_price_flow_detector(_ctx({"oi_change": -0.10}, bars=bars))
    assert evidence is not None
    assert evidence.score == pytest.approx(math.tanh(1.0) * 0.5, abs=1e-9)


def test_flow_short_buildup_is_bearish_full_conviction():
    """Price down, OI up -> short buildup -> bearish, full conviction."""
    bars = [_bar(0, 100.0), _bar(1, 99.0)]
    evidence = oi_price_flow_detector(_ctx({"oi_change": 0.10}, bars=bars))
    assert evidence is not None
    assert evidence.score == pytest.approx(math.tanh(-0.01 / 0.01), abs=1e-9)


def test_flow_long_unwinding_is_bearish_half_conviction():
    """Price down, OI down -> long unwinding -> bearish, half conviction.
    score = tanh(-1) * 0.5."""
    bars = [_bar(0, 100.0), _bar(1, 99.0)]
    evidence = oi_price_flow_detector(_ctx({"oi_change": -0.10}, bars=bars))
    assert evidence is not None
    assert evidence.score == pytest.approx(math.tanh(-1.0) * 0.5, abs=1e-9)


def test_flow_none_with_insufficient_bars():
    assert oi_price_flow_detector(_ctx({"oi_change": 0.1}, bars=[_bar(0, 100.0)])) is None


def test_flow_none_without_oi_change():
    bars = [_bar(0, 100.0), _bar(1, 101.0)]
    assert oi_price_flow_detector(_ctx({}, bars=bars)) is None


def test_flow_none_when_price_unchanged():
    bars = [_bar(0, 100.0), _bar(1, 100.0)]
    assert oi_price_flow_detector(_ctx({"oi_change": 0.1}, bars=bars)) is None
