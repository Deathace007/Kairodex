import datetime
from decimal import Decimal

import pytest

from kairodex.core.enums import Segment
from kairodex.data.types import Bar
from kairodex.features.compute.price_action import (
    opening_range_position,
    price_acceptance,
    trend_state_strength,
    volume_profile_poc_distance,
    vwap_position,
)
from kairodex.features.types import FeatureContext

_T0 = datetime.datetime(2026, 8, 5, 9, 15, tzinfo=datetime.UTC)


def _bar(
    minutes: int, h: float, low: float, c: float, volume: float, o: float | None = None
) -> Bar:
    return Bar(
        ts=_T0 + datetime.timedelta(minutes=minutes),
        open=Decimal(str(o if o is not None else c)),
        high=Decimal(str(h)),
        low=Decimal(str(low)),
        close=Decimal(str(c)),
        volume=int(volume),
    )


def _ctx(bars: list[Bar], session_open_ts: datetime.datetime | None = None) -> FeatureContext:
    return FeatureContext(
        as_of=bars[-1].ts,
        segment=Segment.NSE_INDEX,
        underlying_bars=bars,
        session_open_ts=session_open_ts,
    )


def test_trend_state_strength_matches_independent_ema_reference():
    """Cross-checked against a standalone EMA loop written independently
    of the production `_ema` helper (same standard formula, different
    code) rather than hand-arithmetic — EMA over enough bars to matter is
    tedious to verify by hand without just re-deriving the same loop."""
    closes = [
        100.0, 102, 101, 105, 108, 107, 110, 112, 111, 115, 118,
        120, 122, 125, 128, 130, 129, 133, 135, 138, 140,
    ]  # fmt: skip
    bars = [_bar(i, c + 1, c - 1, c, 1000) for i, c in enumerate(closes)]

    def reference_ema(values: list[float], period: int) -> float:
        a = 2.0 / (period + 1)
        v = values[0]
        for x in values[1:]:
            v = a * x + (1 - a) * v
        return v

    fast, slow = 8, 21
    ema_fast, ema_slow = reference_ema(closes, fast), reference_ema(closes, slow)
    expected = (ema_fast - ema_slow) / ema_slow
    result = trend_state_strength(_ctx(bars), fast=fast, slow=slow)
    assert result == pytest.approx(expected, abs=1e-12)


def test_trend_state_strength_none_when_not_enough_bars():
    bars = [_bar(0, 101, 99, 100, 1000)]
    assert trend_state_strength(_ctx(bars), fast=2, slow=5) is None


def test_vwap_position_hand_computed():
    """2 bars, typical price = close (H=L=C): vwap=(100*10+110*10)/20=105.
    Volume-weighted variance = (10*(100-105)^2+10*(110-105)^2)/20 = 25,
    band=5. spot=last close=110 -> position=(110-105)/5=1.0 exactly."""
    bars = [_bar(0, 100, 100, 100, 10), _bar(1, 110, 110, 110, 10)]
    result = vwap_position(_ctx(bars, session_open_ts=_T0))
    assert result == pytest.approx(1.0, abs=1e-9)


def test_vwap_position_none_without_session_open():
    bars = [_bar(0, 100, 100, 100, 10)]
    assert vwap_position(FeatureContext(as_of=bars[0].ts, segment=Segment.NSE_INDEX)) is None


def test_opening_range_position_hand_computed():
    """Opening range = bars in [T0, T0+15min): highs 105,110,108 -> OR_high=110;
    lows 95,98,100 -> OR_low=95; span=15. Spot (last bar, at +20min) close=102.
    position = (102-95)/15 = 7/15 = 0.466667."""
    bars = [
        _bar(0, 105, 95, 100, 10),
        _bar(5, 110, 98, 103, 10),
        _bar(10, 108, 100, 105, 10),
        _bar(15, 106, 101, 104, 10),  # at the boundary, excluded (< cutoff, not <=)
        _bar(20, 103, 100, 102, 10),
    ]
    result = opening_range_position(_ctx(bars, session_open_ts=_T0), minutes=15)
    assert result == pytest.approx(7.0 / 15.0, abs=1e-9)


def test_volume_profile_poc_distance_hand_computed():
    """closes [100,100,100,110,110] volumes [10,10,10,5,5], bins=2 over
    [100,110] -> bin_width=5. bin0 [100,105) gets the three 100s: 30 vol.
    bin1 gets the two 110s (clamped into the last bin): 10 vol. POC bin=0,
    center = 100 + 0.5*5 = 102.5. Spot = last close = 110.
    (110-102.5)/102.5 = 7.5/102.5 = 0.0731707."""
    bars = [
        _bar(0, 100, 100, 100, 10),
        _bar(1, 100, 100, 100, 10),
        _bar(2, 100, 100, 100, 10),
        _bar(3, 110, 110, 110, 5),
        _bar(4, 110, 110, 110, 5),
    ]
    result = volume_profile_poc_distance(_ctx(bars, session_open_ts=_T0), bins=2)
    assert result == pytest.approx(7.5 / 102.5, abs=1e-9)


def test_price_acceptance_hand_computed():
    """closes [99,101,105,95,100] (spot=last=100), volumes all 10,
    band_pct=0.01 -> threshold=1. Within 1 of 100: 99(diff1,yes),
    101(diff1,yes), 105(no), 95(no), 100(diff0,yes) -> accepted=30/50=0.6."""
    bars = [
        _bar(0, 99, 99, 99, 10),
        _bar(1, 101, 101, 101, 10),
        _bar(2, 105, 105, 105, 10),
        _bar(3, 95, 95, 95, 10),
        _bar(4, 100, 100, 100, 10),
    ]
    result = price_acceptance(_ctx(bars, session_open_ts=_T0), band_pct=0.01)
    assert result == pytest.approx(0.6, abs=1e-9)
