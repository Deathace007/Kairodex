"""Approximate NSE/US session windows — no holiday/half-day calendar
(ARCHITECTURE.md §6's `trading_calendar` sync doesn't exist yet, same
documented gap named in `kairodex.risk.gates.session_window_gate`'s own
docstring), just "is this instant inside the market's own regular hours
today." Shared by the two places that both needed this and, until now,
each carried their own copy: `kairodex.risk.loader.is_session_open` (the
live trading gate — every entry signal passes through this) and
`kairodex.analytics.breakdowns` (the "which third of the session"
bucketer). Caught live: a P6 subagent review fixed the analytics copy's
US window to be DST-aware via `zoneinfo` (a fixed 13:30-20:00 UTC window
is EDT-only — wrong by an hour every winter), but the *other* copy, in
the actual live gate, still had the stale fixed window and a comment
that had the EST/EDT labeling backwards. One shared implementation now,
so a DST fix can't land in one copy and silently miss the other again.
"""

from __future__ import annotations

import datetime
import zoneinfo

from kairodex.core.enums import Market

# NSE: fixed IST offset (UTC+5:30), no DST to account for.
_IST = zoneinfo.ZoneInfo("Asia/Kolkata")
_NSE_SESSION = (datetime.time(9, 15), datetime.time(15, 30))  # 09:15-15:30 IST

# US: real Eastern local time via zoneinfo, not a fixed UTC window — a
# fixed 13:30-20:00 UTC window is exactly right in EDT (roughly Mar-Nov)
# but an hour early/late in EST (roughly Nov-Mar), since EST is UTC-5
# vs. EDT's UTC-4. `zoneinfo` resolves the correct offset per calendar
# date automatically.
_NY = zoneinfo.ZoneInfo("America/New_York")
_US_SESSION = (datetime.time(9, 30), datetime.time(16, 0))  # 09:30-16:00 ET


def _market_zone(market: Market) -> zoneinfo.ZoneInfo:
    return _IST if market is Market.NSE else _NY


def local_date_for(market: Market, ts: datetime.datetime) -> datetime.date:
    """Which calendar date `ts` falls on on the market's own clock — not
    UTC's. A tick/trade near midnight UTC can be on a different calendar
    date in IST or ET than in UTC, and the session window has to be
    looked up for the *market's* day."""
    return ts.astimezone(_market_zone(market)).date()


def session_window_utc(
    market: Market, local_date: datetime.date
) -> tuple[datetime.datetime, datetime.datetime]:
    """The real session open/close, in UTC, for one calendar date in the
    market's own local timezone."""
    open_t, close_t = _NSE_SESSION if market is Market.NSE else _US_SESSION
    zone = _market_zone(market)
    return (
        datetime.datetime.combine(local_date, open_t, tzinfo=zone).astimezone(datetime.UTC),
        datetime.datetime.combine(local_date, close_t, tzinfo=zone).astimezone(datetime.UTC),
    )


def is_session_open_now(market: Market, now: datetime.datetime) -> bool:
    """Is `now` (any tz-aware instant) inside the market's regular
    session, today in the market's own local date. Used both to gate
    live trading (`risk.loader.is_session_open`'s fallback) and to
    bucket a past trade's entry time (`analytics.breakdowns`)."""
    open_dt, close_dt = session_window_utc(market, local_date_for(market, now))
    return open_dt <= now <= close_dt
