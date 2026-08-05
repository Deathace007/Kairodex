import datetime
import statistics
from decimal import Decimal

import pytest

from kairodex.core.enums import Segment
from kairodex.data.types import Bar
from kairodex.features.compute.relative import index_correlation, relative_strength_vs_index
from kairodex.features.types import FeatureContext

_T0 = datetime.datetime(2026, 8, 5, 9, 15, tzinfo=datetime.UTC)


def _bars(closes: list[float]) -> list[Bar]:
    return [
        Bar(
            ts=_T0 + datetime.timedelta(minutes=i),
            open=Decimal(str(c)),
            high=Decimal(str(c)),
            low=Decimal(str(c)),
            close=Decimal(str(c)),
            volume=1000,
        )
        for i, c in enumerate(closes)
    ]


def _ctx(underlying: list[float], index: list[float]) -> FeatureContext:
    u_bars = _bars(underlying)
    return FeatureContext(
        as_of=u_bars[-1].ts,
        segment=Segment.NSE_STOCK,
        underlying_bars=u_bars,
        index_bars=_bars(index),
    )


def test_relative_strength_hand_computed():
    """underlying 100->110 = +10%; index 100->105 = +5%; relative = 0.05."""
    result = relative_strength_vs_index(_ctx([100, 110], [100, 105]))
    assert result == pytest.approx(0.05, abs=1e-9)


def test_relative_strength_none_with_too_few_bars():
    assert relative_strength_vs_index(_ctx([100], [100])) is None


def test_index_correlation_perfect_positive():
    """Underlying's *log* return is exactly 3x the index's every period
    (close_i = close_{i-1} * (index_i/index_{i-1})**3) -> perfectly
    (positively) correlated, since the feature correlates log returns,
    not simple returns — those two are only approximately proportional
    for small moves, so this needs to be exact in log-return space."""
    index = [100, 101, 99, 103, 102, 106]
    underlying = [100.0]
    for i in range(1, len(index)):
        underlying.append(underlying[-1] * (index[i] / index[i - 1]) ** 3)
    result = index_correlation(_ctx(underlying, index))
    assert result == pytest.approx(1.0, abs=1e-9)


def test_index_correlation_matches_stdlib_reference():
    """Cross-checked against statistics.correlation on the same log
    returns computed independently in the test (not by importing the
    production module's helper)."""
    underlying = [100, 103, 101, 107, 104, 110, 108]
    index = [200, 198, 202, 199, 205, 201, 208]
    import math

    u_returns = [math.log(underlying[i] / underlying[i - 1]) for i in range(1, len(underlying))]
    i_returns = [math.log(index[i] / index[i - 1]) for i in range(1, len(index))]
    expected = statistics.correlation(u_returns, i_returns)
    result = index_correlation(_ctx(underlying, index))
    assert result == pytest.approx(expected, abs=1e-9)


def test_index_correlation_none_when_lengths_differ():
    assert index_correlation(_ctx([100, 101, 102], [100, 101])) is None
