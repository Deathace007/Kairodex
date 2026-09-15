"""NSE/US session windows. NSE trading holidays come from
`config/nse_holidays.yaml` (NSE's own list, added 2026-09-15 after the
engine traded the 09-14 holiday); US has no holiday calendar (segment
dormant), and neither market models half-days or special sessions.

Shared by the two places that both needed this and, until now, each
carried their own copy: `kairodex.risk.loader.is_session_open` (the
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
from functools import lru_cache
from pathlib import Path

import yaml

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


# CWD-relative, same convention as config/segments (processes run from the repo root).
_NSE_HOLIDAYS_FILE = Path("config/nse_holidays.yaml")


@lru_cache
def nse_holidays() -> frozenset[datetime.date]:
    with _NSE_HOLIDAYS_FILE.open() as f:
        raw = yaml.safe_load(f) or {}
    return frozenset(d for dates in raw.values() for d in (dates or []))


def is_market_holiday(market: Market, local_date: datetime.date) -> bool:
    return market is Market.NSE and local_date in nse_holidays()


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


def session_length_secs(market: Market) -> float:
    """Length of one regular session in seconds — the unit
    `max_holding_sessions` is denominated in. A fixed local-time span
    (NSE 6h15m, US 6h30m), so no date is needed: DST shifts *when* the
    session happens, never how long it lasts."""
    open_t, close_t = _NSE_SESSION if market is Market.NSE else _US_SESSION
    epoch = datetime.date(2000, 1, 3)
    return (
        datetime.datetime.combine(epoch, close_t) - datetime.datetime.combine(epoch, open_t)
    ).total_seconds()


def session_seconds_between(
    market: Market, start: datetime.datetime, end: datetime.datetime
) -> float:
    """Seconds of *open market* between two instants — weekends and
    overnight gaps excluded.

    Wall-clock age and market age are wildly different things for a
    position, and the engine measured the wrong one. `max_holding_secs`
    was a plain `(now - opened_at)` against a 3-calendar-day guard, so a
    Thursday entry died at Monday's open having seen two sessions, and
    every one of the four TIME_EXITs on 2026-08-10 fired between 09:17
    and 09:19 IST — the widest-spread, gappiest minutes of the week, and
    the worst possible moment to be a forced seller. All four were
    losses (trades 2, 3, 7, 8; -Rs 5,395 between them).

    Counted by walking calendar dates rather than integrating, because
    the windows are per-local-date (DST moves the US one) and a position
    is never held long enough for the loop to matter. NSE holidays
    (`nse_holidays`) are excluded like weekends."""
    if end <= start:
        return 0.0
    total = 0.0
    day = local_date_for(market, start)
    last_day = local_date_for(market, end)
    while day <= last_day:
        if day.weekday() < 5 and not is_market_holiday(market, day):
            open_dt, close_dt = session_window_utc(market, day)
            overlap_start = max(open_dt, start)
            overlap_end = min(close_dt, end)
            if overlap_end > overlap_start:
                total += (overlap_end - overlap_start).total_seconds()
        day += datetime.timedelta(days=1)
    return total


def session_minutes_since_open(market: Market, now: datetime.datetime) -> float | None:
    """How far into today's session `now` is, in minutes. `None` when the
    market is closed — callers treat that as "not a tradable moment"
    rather than as zero."""
    if not is_session_open_now(market, now):
        return None
    open_dt, _ = session_window_utc(market, local_date_for(market, now))
    return (now - open_dt).total_seconds() / 60


def is_session_open_now(market: Market, now: datetime.datetime) -> bool:
    """Is `now` (any tz-aware instant) inside the market's regular
    session, today in the market's own local date. Used both to gate
    live trading (`risk.loader.is_session_open`'s fallback) and to
    bucket a past trade's entry time (`analytics.breakdowns`).

    Weekends are closed for both markets, and NSE holidays from
    `config/nse_holidays.yaml` (an irregular exception needing the
    exchange's own list, unlike the regular weekly schedule). Without the
    weekend check the time-of-day check alone answered True at e.g. 09:15
    IST on a Saturday, so the engine would have evaluated and logged
    signals against a shut exchange all weekend.

    The weekday is taken on the *market's* local date, not UTC's: at
    2026-08-08 00:30 IST it is already Saturday in Mumbai while still
    Friday in UTC, and NSE's schedule follows Mumbai."""
    local_date = local_date_for(market, now)
    if local_date.weekday() >= 5:  # 5=Saturday, 6=Sunday
        return False
    if is_market_holiday(market, local_date):
        return False
    open_dt, close_dt = session_window_utc(market, local_date)
    return open_dt <= now <= close_dt
