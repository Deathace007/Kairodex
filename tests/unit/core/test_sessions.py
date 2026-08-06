import datetime

from kairodex.core.enums import Market
from kairodex.core.sessions import is_session_open_now, local_date_for, session_window_utc


def test_nse_session_window_is_fixed_ist_offset():
    open_dt, close_dt = session_window_utc(Market.NSE, datetime.date(2026, 8, 6))
    # 09:15-15:30 IST = 03:45-10:00 UTC, no DST either way of the year
    assert open_dt == datetime.datetime(2026, 8, 6, 3, 45, tzinfo=datetime.UTC)
    assert close_dt == datetime.datetime(2026, 8, 6, 10, 0, tzinfo=datetime.UTC)


def test_us_session_window_edt_summer():
    # 2026-08-06 is EDT (UTC-4): 09:30-16:00 ET = 13:30-20:00 UTC
    open_dt, close_dt = session_window_utc(Market.US, datetime.date(2026, 8, 6))
    assert open_dt == datetime.datetime(2026, 8, 6, 13, 30, tzinfo=datetime.UTC)
    assert close_dt == datetime.datetime(2026, 8, 6, 20, 0, tzinfo=datetime.UTC)


def test_us_session_window_est_winter():
    # 2026-01-15 is EST (UTC-5): 09:30-16:00 ET = 14:30-21:00 UTC — the
    # exact case the old hardcoded-13:30-20:00 fallback got wrong.
    open_dt, close_dt = session_window_utc(Market.US, datetime.date(2026, 1, 15))
    assert open_dt == datetime.datetime(2026, 1, 15, 14, 30, tzinfo=datetime.UTC)
    assert close_dt == datetime.datetime(2026, 1, 15, 21, 0, tzinfo=datetime.UTC)


def test_is_session_open_now_nse_inside_and_outside():
    inside = datetime.datetime(2026, 8, 6, 5, 0, tzinfo=datetime.UTC)  # 10:30 IST
    outside = datetime.datetime(2026, 8, 6, 1, 0, tzinfo=datetime.UTC)  # 06:30 IST
    assert is_session_open_now(Market.NSE, inside) is True
    assert is_session_open_now(Market.NSE, outside) is False


def test_is_session_open_now_us_winter_hour_that_a_fixed_utc_window_would_get_wrong():
    # 20:15 UTC on 2026-01-15 is 15:15 ET (EST) — genuinely inside the
    # session — but the OLD hardcoded 13:30-20:00 UTC fallback would
    # have called this already closed.
    still_open = datetime.datetime(2026, 1, 15, 20, 15, tzinfo=datetime.UTC)
    assert is_session_open_now(Market.US, still_open) is True


def test_local_date_for_resolves_market_local_day_not_utc():
    # 02:00 UTC on 2026-08-06 is 21:00 ET on 2026-08-05 — the previous
    # day in New York, even though it's still 2026-08-06 in UTC.
    ts = datetime.datetime(2026, 8, 6, 2, 0, tzinfo=datetime.UTC)
    assert local_date_for(Market.US, ts) == datetime.date(2026, 8, 5)
    assert local_date_for(Market.NSE, ts) == datetime.date(2026, 8, 6)  # 07:30 IST, same day
