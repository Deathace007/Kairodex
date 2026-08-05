"""Every expected value here is worked by hand (see each test's
docstring for the arithmetic), not just eyeballed for plausibility."""

import datetime
import math
from decimal import Decimal

import pytest

from kairodex.core.enums import Segment
from kairodex.data.types import Bar
from kairodex.features.compute.volatility import atr, realized_vol, volatility_regime
from kairodex.features.types import FeatureContext

_T0 = datetime.datetime(2026, 8, 5, 9, 15, tzinfo=datetime.UTC)


def _bar(i: int, o: float, h: float, low: float, c: float, delta_s: int = 3600) -> Bar:
    return Bar(
        ts=_T0 + datetime.timedelta(seconds=i * delta_s),
        open=Decimal(str(o)),
        high=Decimal(str(h)),
        low=Decimal(str(low)),
        close=Decimal(str(c)),
        volume=1000,
    )


def _ctx(bars: list[Bar]) -> FeatureContext:
    return FeatureContext(as_of=bars[-1].ts, segment=Segment.NSE_INDEX, underlying_bars=bars)


def test_atr_wilder_smoothing_matches_hand_computation():
    """5 bars -> 4 true ranges [7, 3, 6, 3] (closes 100,102,103,106,105 with
    highs/lows chosen so TR is driven by the close-to-close gap each time).
    Wilder ATR(period=3): first = mean(7,3,6) = 16/3 = 5.3333..., then
    smoothed with TR4=3: (5.3333*2 + 3) / 3 = 4.55556."""
    bars = [
        _bar(0, 99, 100, 98, 100),
        _bar(1, 100, 105, 98, 102),  # TR = max(7, |105-100|=5, |98-100|=2) = 7
        _bar(2, 102, 104, 101, 103),  # TR = max(3, |104-102|=2, |101-102|=1) = 3
        _bar(3, 103, 108, 102, 106),  # TR = max(6, |108-103|=5, |102-103|=1) = 6
        _bar(4, 106, 107, 104, 105),  # TR = max(3, |107-106|=1, |104-106|=2) = 3
    ]
    result = atr(_ctx(bars), period=3)
    assert result == pytest.approx(4.55556, abs=1e-4)


def test_atr_none_when_not_enough_bars():
    bars = [_bar(0, 100, 101, 99, 100), _bar(1, 100, 102, 99, 101)]
    assert atr(_ctx(bars), period=14) is None


def test_realized_vol_exact_log_return_construction():
    """Closes constructed as 100, 100*e^0.1, 100*e^0.1*e^-0.1 -> log
    returns are *exactly* +0.1 and -0.1 by construction (not approximately
    — math.log(math.exp(x)) == x). Sample variance of [0.1,-0.1] (mean 0):
    ((0.1)^2 + (-0.1)^2) / (2-1) = 0.02, stdev = sqrt(0.02) = 0.1414214.
    Hourly bars (delta=3600s) -> periods_per_year = 365.25*24*3600/3600 =
    8766, sqrt(8766) = 93.6265. Expected = 0.1414214 * 93.6265 = 13.241."""
    c0 = 100.0
    c1 = c0 * math.exp(0.1)
    c2 = c1 * math.exp(-0.1)
    bars = [_bar(0, c0, c0, c0, c0), _bar(1, c1, c1, c1, c1), _bar(2, c2, c2, c2, c2)]
    result = realized_vol(_ctx(bars))
    expected = math.sqrt(0.02) * math.sqrt(365.25 * 24 * 3600 / 3600)
    assert result == pytest.approx(expected, abs=1e-6)


def test_realized_vol_none_when_not_enough_bars():
    bars = [_bar(0, 100, 100, 100, 100), _bar(1, 101, 101, 101, 101)]
    assert realized_vol(_ctx(bars)) is None


def test_volatility_regime_ratio():
    """Closes chosen so TR reduces to |close_i - close_{i-1}| (high=low=
    close): gaps [2, 2, 2, 8]. short_period=2 -> mean(last 2)=mean(2,8)=5.
    long_period=4 -> mean(all 4)=mean(2,2,2,8)=3.5. Ratio = 5/3.5 = 1.42857."""
    closes = [100, 102, 100, 102, 110]  # gaps: 2, 2, 2, 8
    bars = [_bar(i, c, c, c, c) for i, c in enumerate(closes)]
    result = volatility_regime(_ctx(bars), short_period=2, long_period=4)
    assert result == pytest.approx(5.0 / 3.5, abs=1e-9)


def test_volatility_regime_none_when_not_enough_bars():
    bars = [_bar(0, 100, 100, 100, 100), _bar(1, 101, 101, 101, 101)]
    assert volatility_regime(_ctx(bars), short_period=2, long_period=10) is None
