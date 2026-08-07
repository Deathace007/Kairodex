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


def test_relative_strength_uses_only_the_shared_instants():
    """Unequal lengths are no longer a refusal — they are the normal shape
    of a feed that skips untraded minutes. The index series ends a minute
    early, so the comparison runs over minutes 0-1 only: underlying
    100->105 = +5%, index 100->102 = +2%, relative = +0.03. The 110 has no
    counterpart and is dropped rather than paired with a stale index
    close."""
    assert relative_strength_vs_index(_ctx([100, 105, 110], [100, 102])) == pytest.approx(0.03)


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


def test_index_correlation_none_when_too_few_shared_instants():
    """Two shared minutes is one log return each — a correlation of one
    point is not a correlation, so this still refuses. What changed is the
    reason: too little overlap, not merely unequal lengths."""
    assert index_correlation(_ctx([100, 101, 102], [100, 101])) is None


def _sparse_ctx() -> FeatureContext:
    """An LSE-shaped pair: the index prints every minute, the underlying
    skips the minutes it did not trade. Four shared instants, different
    lengths — the exact shape that was returning None live."""
    index = _bars([100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 106.0])
    u_all = _bars([100.0, 0.0, 102.0, 0.0, 106.0, 0.0, 110.0])
    underlying = [u_all[i] for i in (0, 2, 4, 6)]  # minutes 0, 2, 4, 6
    return FeatureContext(
        as_of=index[-1].ts,
        segment=Segment.US_STOCK,
        underlying_bars=underlying,
        index_bars=index,
    )


def test_relative_strength_survives_a_sparse_underlying():
    """The bug: `len(underlying) != len(index)` returned None for every US
    underlying (live 2026-08-07: SPY 3798 bars, NVDA 3840, BAC 2343 over the
    same window), killing the RELATIVE_STRENGTH family on US entirely.
    Paired by timestamp, minute 0 -> minute 6 is underlying +10% against
    index +6% = +0.04."""
    assert relative_strength_vs_index(_sparse_ctx()) == pytest.approx(0.04)


def test_index_correlation_survives_a_sparse_underlying():
    """Same guard, same kill, same fix — correlation over the four shared
    instants, not None."""
    value = index_correlation(_sparse_ctx())
    assert value is not None
    assert -1.0 <= value <= 1.0


def test_no_shared_timestamps_still_returns_none():
    """Dropping the length guard must not mean pairing unrelated windows:
    an index series offset by a full day shares no instant with the
    underlying, so there is nothing honest to compare."""
    u_bars = _bars([100.0, 101.0, 102.0])
    shifted = [
        Bar(
            ts=b.ts + datetime.timedelta(days=1),
            open=b.open,
            high=b.high,
            low=b.low,
            close=b.close,
            volume=b.volume,
        )
        for b in _bars([100.0, 101.0, 102.0])
    ]
    ctx = FeatureContext(
        as_of=u_bars[-1].ts,
        segment=Segment.US_STOCK,
        underlying_bars=u_bars,
        index_bars=shifted,
    )
    assert relative_strength_vs_index(ctx) is None
    assert index_correlation(ctx) is None
