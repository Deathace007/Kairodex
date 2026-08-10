"""Session-time arithmetic — the measure the position monitor's time rules
switched to after the wall-clock version fired four TIME_EXITs at Monday's
opening bell on 2026-08-10 (PROGRESS.md §15).

Every expected value is worked by hand in the test's own docstring.
"""

import datetime

import pytest

from kairodex.core.enums import Market
from kairodex.core.sessions import (
    session_length_secs,
    session_minutes_since_open,
    session_seconds_between,
)

_HOUR = 3600


def test_session_lengths_are_the_documented_windows():
    """NSE 09:15-15:30 = 6h15m; US 09:30-16:00 = 6h30m."""
    assert session_length_secs(Market.NSE) == 6.25 * _HOUR
    assert session_length_secs(Market.US) == 6.5 * _HOUR


def test_within_one_session_matches_wall_clock():
    """10:00-11:00 UTC on a Wednesday is 15:30... no: 10:00 UTC = 15:30 IST
    is the close, so use 05:00-06:00 UTC = 10:30-11:30 IST, squarely
    inside the session."""
    start = datetime.datetime(2026, 8, 5, 5, 0, tzinfo=datetime.UTC)
    end = start + datetime.timedelta(hours=1)
    assert session_seconds_between(Market.NSE, start, end) == pytest.approx(_HOUR)


def test_overnight_gap_is_excluded():
    """Wednesday 14:00 IST -> Thursday 10:15 IST. Wednesday contributes
    14:00-15:30 = 1h30m, Thursday 09:15-10:15 = 1h. Total 2h30m — against
    20h15m of wall-clock."""
    start = datetime.datetime(2026, 8, 5, 8, 30, tzinfo=datetime.UTC)  # 14:00 IST Wed
    end = datetime.datetime(2026, 8, 6, 4, 45, tzinfo=datetime.UTC)  # 10:15 IST Thu
    assert (end - start).total_seconds() == pytest.approx(20.25 * _HOUR)
    assert session_seconds_between(Market.NSE, start, end) == pytest.approx(2.5 * _HOUR)


def test_weekend_contributes_nothing():
    """The live case. Trade 2 opened Thursday 2026-08-06 09:15 IST and was
    time-exited Monday 08-10 09:17 IST — four calendar days, but only
    Thursday and Friday were sessions: 2 x 6h15m = 12h30m, plus the two
    minutes of Monday. A 3-session guard (18h45m) must not have fired."""
    opened = datetime.datetime(2026, 8, 6, 3, 45, tzinfo=datetime.UTC)  # 09:15 IST Thu
    exited = datetime.datetime(2026, 8, 10, 3, 47, tzinfo=datetime.UTC)  # 09:17 IST Mon
    assert (exited - opened).total_seconds() > 3 * 24 * _HOUR  # wall-clock: past a 3-day guard
    held = session_seconds_between(Market.NSE, opened, exited)
    assert held == pytest.approx(2 * 6.25 * _HOUR + 2 * 60)
    assert held < 3 * session_length_secs(Market.NSE)  # session time: not yet


def test_us_window_follows_dst():
    """09:30-16:00 ET is 13:30-20:00 UTC in EDT (August) and 14:30-21:00
    UTC in EST (January) — the same 6h30m either way, but a fixed-UTC
    window would put a January noon-ET instant outside the session."""
    jan = datetime.date(2026, 1, 14)
    aug = datetime.date(2026, 8, 12)
    for day, open_utc_hour in ((jan, 14), (aug, 13)):
        start = datetime.datetime(
            day.year, day.month, day.day, open_utc_hour, 30, tzinfo=datetime.UTC
        )
        end = start + datetime.timedelta(hours=6, minutes=30)
        assert session_seconds_between(Market.US, start, end) == pytest.approx(6.5 * _HOUR)


def test_zero_and_reversed_ranges():
    now = datetime.datetime(2026, 8, 5, 5, 0, tzinfo=datetime.UTC)
    assert session_seconds_between(Market.NSE, now, now) == 0.0
    assert session_seconds_between(Market.NSE, now, now - datetime.timedelta(hours=1)) == 0.0


def test_minutes_since_open_is_none_when_closed():
    """Saturday — no session to be any minutes into."""
    saturday = datetime.datetime(2026, 8, 8, 5, 0, tzinfo=datetime.UTC)
    assert session_minutes_since_open(Market.NSE, saturday) is None


def test_minutes_since_open_measures_from_the_bell():
    """09:15 IST + 20 min = 09:35 IST = 04:05 UTC."""
    now = datetime.datetime(2026, 8, 5, 4, 5, tzinfo=datetime.UTC)
    assert session_minutes_since_open(Market.NSE, now) == pytest.approx(20.0)
