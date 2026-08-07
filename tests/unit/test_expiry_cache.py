"""T1 poll economics: the per-day expiry cache, and the request budget the
US poll interval is set from.

`list_expiries` costs a full unfiltered chain fetch (plus up to 14 probe
calls for SPY/QQQ) to learn dates that change about weekly; re-asking every
60s was a third of LSE's entire daily budget."""

import datetime

from kairodex.core.enums import Market
from kairodex.data.recorder import (
    BAR_REFRESH_INTERVAL,
    T1_POLL_INTERVAL,
    T1_POLL_INTERVAL_BY_MARKET,
    _expiries_cached,
    recover_underlying_bars,
)
from kairodex.store.models import Instrument


class _CountingClient:
    def __init__(self) -> None:
        self.calls = 0

    async def list_expiries(self, key: str) -> list[datetime.date]:
        self.calls += 1
        return [datetime.date(2026, 8, 14)]


async def test_second_lookup_same_day_does_not_refetch():
    client = _CountingClient()
    cache: dict[str, tuple[datetime.date, list[datetime.date]]] = {}
    first = await _expiries_cached(client, "AAPL", cache)  # type: ignore[arg-type]
    second = await _expiries_cached(client, "AAPL", cache)  # type: ignore[arg-type]
    assert first == second
    assert client.calls == 1


async def test_each_underlying_is_cached_separately():
    client = _CountingClient()
    cache: dict[str, tuple[datetime.date, list[datetime.date]]] = {}
    await _expiries_cached(client, "AAPL", cache)  # type: ignore[arg-type]
    await _expiries_cached(client, "MSFT", cache)  # type: ignore[arg-type]
    assert client.calls == 2


def test_us_poll_interval_stays_inside_lses_daily_cap():
    """The original outage was not a bug in any one line — it was that
    nobody multiplied. 22 underlyings x 3 calls x 1440 min = ~96k/day
    against a 15k cap, which burns out in 3.7h. This pins the arithmetic so
    a future interval change or watchlist growth trips a test rather than a
    vendor's weekly rolling limit (which takes days, not hours, to clear).

    Deliberately independent of the constants' own comments: it recomputes
    the budget rather than asserting a magic number."""
    lse_daily_cap = 15_000
    underlyings = 22  # us_stock 18 + us_index 4, live 2026-08-06
    expiries_per_underlying = 2  # poll_chain_once's `expiries[:2]`
    us_session_minutes = 6.5 * 60  # 09:30-16:00 ET, gated by is_session_open_now

    interval = T1_POLL_INTERVAL_BY_MARKET[Market.US]
    cycles = us_session_minutes / (interval.total_seconds() / 60)
    per_cycle = underlyings * expiries_per_underlying + 1  # +1 quota check
    # + one list_expiries per underlying per day, thanks to the cache
    calls_per_day = cycles * per_cycle + underlyings
    # + the in-session bar refresh: one candles() call per underlying each
    # time their newest bar ages past BAR_REFRESH_INTERVAL. Bars are metered
    # exactly like chains, so leaving them out of this sum is how the next
    # outage gets built.
    bar_refreshes = us_session_minutes / (BAR_REFRESH_INTERVAL.total_seconds() / 60)
    calls_per_day += bar_refreshes * underlyings

    assert calls_per_day < lse_daily_cap, (
        f"US T1 poll would issue {calls_per_day:.0f} calls/day against a "
        f"{lse_daily_cap} cap — widen the interval or cut the watchlist"
    )
    # Headroom for restarts (each re-runs bar backfill), SPY/QQQ probes, and
    # growth — being just barely under is how this breaks again.
    assert calls_per_day < lse_daily_cap * 0.75


def test_nse_keeps_the_faster_default_interval():
    """Upstox's cap is far larger (0.06% used at the same call rate), so the
    budget fix must not slow NSE down — the shared loop was tuned to the
    tolerant vendor once already, and this pins the asymmetry as deliberate."""
    assert Market.NSE not in T1_POLL_INTERVAL_BY_MARKET
    assert T1_POLL_INTERVAL.total_seconds() == 60


async def test_stale_day_is_refetched():
    """The whole point is that it expires — a cache keyed only by symbol
    would pin yesterday's nearest expiry forever, and the nearest expiry is
    exactly the one that rolls."""
    client = _CountingClient()
    yesterday = datetime.date.today() - datetime.timedelta(days=1)
    cache = {"AAPL": (yesterday, [datetime.date(2026, 1, 1)])}
    result = await _expiries_cached(client, "AAPL", cache)  # type: ignore[arg-type]
    assert client.calls == 1
    assert result == [datetime.date(2026, 8, 14)]


_INSTRUMENT = Instrument(
    instrument_id=1, symbol="NVDA", exchange="US", provider_ids={"lse": "NVDA"}
)
# 2026-08-06 23:59 UTC — the real newest US bar while the 2026-08-07 session
# was already four hours old.
_STALE_TS = datetime.datetime(2026, 8, 6, 23, 59, tzinfo=datetime.UTC)


def _FRESH_TS() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=1)


class _FakeSession:
    """`recover_underlying_bars` only reads one scalar per underlying; the
    batch write short-circuits on an empty bar list, so nothing else of a
    real session is reachable from here."""

    def __init__(self, last_ts: datetime.datetime) -> None:
        self._last_ts = last_ts

    async def scalar(self, _stmt: object) -> datetime.datetime:
        return self._last_ts


class _StaleBarClient:
    """Records which (key, start, end) bar pulls were asked for."""

    def __init__(self) -> None:
        self.pulls: list[tuple[str, datetime.date, datetime.date]] = []

    async def bars(self, key, tf, start, end):  # type: ignore[no-untyped-def]
        self.pulls.append((key, start, end))
        return []


async def test_bars_refresh_when_the_newest_is_older_than_max_age(monkeypatch):
    """The live fault: bars were pulled at process startup and never again,
    so `trend_state_strength` — and with it the whole STRUCTURE detector
    family — was frozen at one value per underlying for the process's
    entire life. Verified live 2026-08-07: four hours into the US session
    the newest US bar was still 2026-08-06 23:59, and every one of the 20
    US underlyings that emitted a signal that day had exactly one distinct
    `trend_structure` score in its evidence.

    A bar older than `max_age` must therefore be refetched even though one
    already exists for today — that "already exists for today" is precisely
    what the old `start >= today` guard refused on."""
    session, client = _FakeSession(_STALE_TS), _StaleBarClient()
    await recover_underlying_bars(
        session, client, "lse", Market.US, [_INSTRUMENT], max_age=BAR_REFRESH_INTERVAL
    )
    assert client.pulls, "a stale bar must trigger a refetch mid-session"
    _, start, end = client.pulls[0]
    assert start == _STALE_TS.date()
    # The vendor's `end` is exclusive: measured live against LSE on
    # 2026-08-07 at 14:33 UTC with the US market open, end=2026-08-07
    # returned 942 bars ending 2026-08-06 23:59 while end=2026-08-08
    # returned 1327 ending 2026-08-07 14:33. Asking for `today` therefore
    # cannot ever reach today's session — the refresh ran, logged success,
    # and wrote nothing.
    assert end > datetime.date.today(), "end is exclusive — today's bars are unreachable"


async def test_bars_not_refetched_while_still_fresh():
    """The other half — without this the refresh spends one metered LSE
    call per underlying on every 120s poll cycle, which is how the quota
    was blown the first time."""
    session, client = _FakeSession(_FRESH_TS()), _StaleBarClient()
    await recover_underlying_bars(
        session, client, "lse", Market.US, [_INSTRUMENT], max_age=BAR_REFRESH_INTERVAL
    )
    assert client.pulls == []
