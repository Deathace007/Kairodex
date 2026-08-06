"""Breakdown-by-dimension over `list[TradeRecord]` — ARCHITECTURE.md
§15's `GET .../analytics/breakdown?by=regime|weekday|session|expiry|
moneyness|vol_regime`. Each `_*_bucket` function is `TradeRecord ->
str | None`; `breakdown()` groups trades by bucket and runs
`performance.summarize` on each group, so this file owns bucketing only,
never its own copy of the performance math.
"""

from __future__ import annotations

import datetime
import zoneinfo
from collections.abc import Callable

from kairodex.analytics import performance
from kairodex.analytics.types import PerformanceSummary, TradeRecord
from kairodex.core.enums import Market

_DIMENSIONS = ("regime", "weekday", "session", "expiry", "moneyness", "vol_regime")

# NSE: fixed IST offset, no DST — a plain UTC window is exact, same
# hardcoded approximation `kairodex.risk.loader.is_session_open` uses (no
# `trading_calendar` data wired into this path either, same documented
# gap — this doesn't know about holidays/half-days).
_NSE_SESSION = (datetime.time(3, 45), datetime.time(10, 0))  # UTC = 09:15-15:30 IST

# US: real Eastern local time via stdlib `zoneinfo`, not a fixed UTC
# window — a P5 subagent review caught that a fixed 13:30-20:00 UTC
# window is EDT-only; under EST (roughly Nov-Mar) the real session is
# 14:30-21:00 UTC, so a fixed window silently dropped ~15% of each
# winter session's trades from this breakdown entirely (`frac > 1`) and
# shifted the rest by nearly a full third-of-session bucket. `zoneinfo`
# resolves the correct offset per calendar date automatically.
_NY = zoneinfo.ZoneInfo("America/New_York")
_US_SESSION = (datetime.time(9, 30), datetime.time(16, 0))  # local, DST-aware


def _weekday_bucket(t: TradeRecord) -> str | None:
    return t.opened_at.strftime("%A")


def _session_window_utc(
    market: Market, local_date: datetime.date
) -> tuple[datetime.datetime, datetime.datetime]:
    if market is Market.NSE:
        open_t, close_t = _NSE_SESSION
        return (
            datetime.datetime.combine(local_date, open_t, tzinfo=datetime.UTC),
            datetime.datetime.combine(local_date, close_t, tzinfo=datetime.UTC),
        )
    open_t, close_t = _US_SESSION
    return (
        datetime.datetime.combine(local_date, open_t, tzinfo=_NY).astimezone(datetime.UTC),
        datetime.datetime.combine(local_date, close_t, tzinfo=_NY).astimezone(datetime.UTC),
    )


def _session_bucket(t: TradeRecord) -> str | None:
    market = t.segment.market
    local_date = t.opened_at.astimezone(_NY).date() if market is Market.US else t.opened_at.date()
    open_dt, close_dt = _session_window_utc(market, local_date)
    session_secs = (close_dt - open_dt).total_seconds()
    if session_secs <= 0:
        return None
    elapsed = (t.opened_at - open_dt).total_seconds()
    frac = elapsed / session_secs
    if frac < 0 or frac > 1:
        return None  # opened outside the approximate window at all — not bucketable
    if frac < 1 / 3:
        return "open"
    if frac < 2 / 3:
        return "mid"
    return "close"


def _expiry_bucket(t: TradeRecord) -> str | None:
    if t.expiry is None:
        return None
    dte = (t.expiry - t.opened_at.date()).days
    if dte <= 2:
        return "0-2d"
    if dte <= 7:
        return "3-7d"
    if dte <= 30:
        return "8-30d"
    return "31d+"  # dte >= 31 — "8-30d" already claims dte == 30


def _moneyness_bucket(t: TradeRecord) -> str | None:
    """`(underlying - strike) / underlying` signed so ITM is always
    positive regardless of call/put — a call is ITM above its strike, a
    put is ITM below, so the put side of the ratio is negated to match."""
    spot = t.underlying_px_entry
    if spot is None or t.strike is None or spot == 0 or t.option_type is None:
        return None
    raw = (spot - float(t.strike)) / spot
    signed = raw if t.option_type == "C" else -raw
    if signed > 0.01:
        return "ITM"
    if signed < -0.01:
        return "OTM"
    return "ATM"


def _vol_regime_bucket(t: TradeRecord) -> str | None:
    v = t.vol_regime_entry
    if v is None:
        return None
    return "expanding" if v > 1.0 else "contracting"  # backtest.metrics' own convention


def _regime_bucket(t: TradeRecord) -> str | None:
    """Trend direction at entry — `trend_state_strength`'s own sign
    convention (positive=uptrend, negative=downtrend). +-1% is a
    ponytail-flagged first-pass "flat" band, not calibrated."""
    v = t.trend_state_entry
    if v is None:
        return None
    if v > 0.01:
        return "uptrend"
    if v < -0.01:
        return "downtrend"
    return "flat"


_BUCKET_FNS: dict[str, Callable[[TradeRecord], str | None]] = {
    "weekday": _weekday_bucket,
    "session": _session_bucket,
    "expiry": _expiry_bucket,
    "moneyness": _moneyness_bucket,
    "vol_regime": _vol_regime_bucket,
    "regime": _regime_bucket,
}


def breakdown(trades: list[TradeRecord], by: str) -> dict[str, PerformanceSummary]:
    if by not in _BUCKET_FNS:
        raise ValueError(f"unknown breakdown dimension {by!r}, must be one of {_DIMENSIONS}")
    fn = _BUCKET_FNS[by]
    groups: dict[str, list[TradeRecord]] = {}
    for t in trades:
        key = fn(t)
        if key is None:
            continue
        groups.setdefault(key, []).append(t)
    return {key: performance.summarize(group) for key, group in groups.items()}
