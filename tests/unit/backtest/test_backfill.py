"""`_resolve_one` is the whole of the backfill's judgement — the rest of
the module is a bar query and a loop. These pin the two rules that are
not obvious from reading it, and the lookahead guard."""

from __future__ import annotations

import datetime
from decimal import Decimal

from kairodex.backtest.backfill import _resolve_one
from kairodex.core.enums import Market, Segment, Side
from kairodex.data.types import Bar, Timeframe
from kairodex.store.models import Signal

_LOOKBACK = 14  # atr's own period — the smallest window that resolves an ATR at all
_IST_OFFSET = datetime.timedelta(hours=5, minutes=30)


def _bar(ts: datetime.datetime, close: float, *, high: float, low: float) -> Bar:
    return Bar(
        ts=ts,
        open=Decimal(str(close)),
        high=Decimal(str(high)),
        low=Decimal(str(low)),
        close=Decimal(str(close)),
        volume=1000,
    )


def _session_bars(day: datetime.date, count: int, start_hhmm: tuple[int, int]) -> list[Bar]:
    """`count` one-minute bars from `start_hhmm` IST, oscillating enough
    to give ATR a non-zero true range to work with."""
    first = datetime.datetime.combine(
        day, datetime.time(*start_hhmm), tzinfo=datetime.timezone(_IST_OFFSET)
    ).astimezone(datetime.UTC)
    bars = []
    for i in range(count):
        close = 100.0 + (i % 2)
        ts = first + datetime.timedelta(minutes=i)
        bars.append(_bar(ts, close, high=close + 1, low=close - 1))
    return bars


def _signal(ts: datetime.datetime) -> Signal:
    return Signal(
        ts=ts,
        segment=Segment.NSE_STOCK,
        strategy_id=1,
        underlying_id=1,
        direction=Side.BUY,
        confidence=Decimal("0.8"),
        decision="TAKEN",
    )


def _resolve(bars: list[Bar], signal: Signal, *, horizon: int = 90):
    return _resolve_one(
        signal,
        bars,
        [b.ts for b in bars],
        market=Market.NSE,
        segment=Segment.NSE_STOCK,
        timeframe=Timeframe.ONE_MIN,
        lookback_bars=_LOOKBACK,
        max_holding_bars=horizon,
        stop_atr_mult=1.0,
        target_atr_mult=2.0,
    )


def test_forward_window_never_crosses_into_the_next_session():
    """The engine is intraday-only (§15g), so a late signal must be
    scored on the runway it actually had. Here the rest of the session is
    flat and the NEXT morning gaps far through the target — if the
    truncation were missing, this would resolve TARGET off bars the
    position could never have been held into."""
    day = datetime.date(2026, 8, 12)
    bars = _session_bars(day, _LOOKBACK + 1 + 5, (15, 10))  # entry + only 5 bars left
    next_day = _session_bars(datetime.date(2026, 8, 13), 30, (9, 15))
    bars += [_bar(b.ts, 500.0, high=501.0, low=499.0) for b in next_day]  # huge overnight gap

    signal = _signal(bars[_LOOKBACK].ts)
    assert _resolve(bars, signal) == "unresolved"


def test_a_truncated_window_that_never_resolved_is_left_null_not_scored_flat():
    """A short window that hit neither stop nor target hasn't resolved.
    Scoring it TIME would systematically label every late-session signal
    as a flat outcome, biasing exactly the part of the day the cutoff
    gate already distrusts."""
    day = datetime.date(2026, 8, 12)
    bars = _session_bars(day, _LOOKBACK + 1 + 10, (14, 0))
    signal = _signal(bars[_LOOKBACK].ts)
    assert _resolve(bars, signal, horizon=90) == "unresolved"


def test_resolves_a_target_hit_inside_the_session():
    day = datetime.date(2026, 8, 12)
    bars = _session_bars(day, _LOOKBACK + 1, (10, 0))
    entry_ts = bars[-1].ts
    # ATR here is ~2.0 (high-low each bar), so a +2 ATR target sits near 105.
    for i in range(1, 20):
        bars.append(_bar(entry_ts + datetime.timedelta(minutes=i), 110.0, high=111.0, low=109.0))

    signal = _signal(entry_ts)
    outcome = _resolve(bars, signal)
    assert isinstance(outcome, dict)
    assert outcome["exit_reason"] == "TARGET"
    assert outcome["bars_held"] == 1
    # The parameters travel with the number — a return_atr is meaningless alone.
    assert outcome["max_holding_bars"] == 90
    assert outcome["timeframe"] == "1m"
    assert float(outcome["return_atr"]) > 0


def test_entry_price_is_the_bar_at_or_before_the_signal_never_after():
    """The engine scored on data up to `signal.ts`; resolving off a later
    bar's close would hand the backfill a look at the answer."""
    day = datetime.date(2026, 8, 12)
    bars = _session_bars(day, _LOOKBACK + 1, (10, 0))
    entry_ts = bars[-1].ts
    for i in range(1, 20):
        bars.append(_bar(entry_ts + datetime.timedelta(minutes=i), 110.0, high=111.0, low=109.0))

    # A signal 30 seconds AFTER the entry bar still resolves off that bar.
    signal = _signal(entry_ts + datetime.timedelta(seconds=30))
    outcome = _resolve(bars, signal)
    assert isinstance(outcome, dict)
    assert Decimal(str(outcome["entry_price"])) == bars[_LOOKBACK].close
