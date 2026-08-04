import datetime

from kairodex.status import _fmt_age

_NOW = datetime.datetime(2026, 8, 4, 10, 0, 0, tzinfo=datetime.UTC)


def test_never_seen():
    assert _fmt_age(None, _NOW) == "never"


def test_seconds_ago():
    ts = _NOW - datetime.timedelta(seconds=5)
    assert _fmt_age(ts, _NOW) == "5s ago"


def test_minutes_ago():
    ts = _NOW - datetime.timedelta(minutes=10)
    assert _fmt_age(ts, _NOW) == "10m ago"


def test_hours_ago():
    ts = _NOW - datetime.timedelta(hours=2, minutes=30)
    assert _fmt_age(ts, _NOW) == "2.5h ago"
