import datetime

import pytest

from kairodex.backtest.clock import ReplayClock


def test_now_returns_start():
    start = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
    assert ReplayClock(start).now() == start


def test_advance_to_moves_forward():
    start = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
    clock = ReplayClock(start)
    later = start + datetime.timedelta(days=1)
    clock.advance_to(later)
    assert clock.now() == later


def test_advance_to_same_timestamp_is_allowed():
    start = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
    clock = ReplayClock(start)
    clock.advance_to(start)
    assert clock.now() == start


def test_advance_to_backward_rejected():
    start = datetime.datetime(2026, 1, 2, tzinfo=datetime.UTC)
    clock = ReplayClock(start)
    with pytest.raises(ValueError, match="cannot move backward"):
        clock.advance_to(start - datetime.timedelta(days=1))
