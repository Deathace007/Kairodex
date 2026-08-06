"""Regression test for the SPY/QQQ dense-chain fallback (live-discovered
2026-08-04): LSE's date-range options() filter silently returns a stale,
capped page for these tickers, but exact-date queries work — see
kairodex/data/lse/client.py's _probe_expiries docstring."""

import datetime

from kairodex.data.lse.client import LSEClient

_TODAY = datetime.date(2026, 8, 4)


class _FakeVendorClient:
    """Stands in for lse.LSE — options() keyed by exact expiry date only,
    mirroring the confirmed-live vendor behavior for dense chains."""

    def __init__(self, live_expiries: set[str]) -> None:
        self.live_expiries = live_expiries
        self.calls: list[str | None] = []

    def options(self, underlying: str, expiry: str | None = None, **kwargs: object) -> list[dict]:
        self.calls.append(expiry)
        if expiry is None:
            return []  # the confirmed-broken unfiltered/range path
        return [{"ticker": "X", "expiry": expiry}] if expiry in self.live_expiries else []


def _client_with_fake(fake: _FakeVendorClient) -> LSEClient:
    client = LSEClient.__new__(LSEClient)
    client._client = fake  # type: ignore[attr-defined]
    return client


async def test_probe_finds_two_expiries_and_stops_early():
    fake = _FakeVendorClient({"2026-08-05", "2026-08-07", "2026-08-10"})
    client = _client_with_fake(fake)
    expiries = await client._probe_expiries("SPY", _TODAY, window_days=14)
    assert expiries == [datetime.date(2026, 8, 5), datetime.date(2026, 8, 7)]
    assert len(fake.calls) == 4  # 8/4 miss, 8/5 hit, 8/6 miss, 8/7 hit -> stop


async def test_probe_stops_at_two_even_with_more_available():
    fake = _FakeVendorClient({"2026-08-04", "2026-08-05", "2026-08-06", "2026-08-07"})
    client = _client_with_fake(fake)
    expiries = await client._probe_expiries("SPY", _TODAY, window_days=14)
    assert expiries == [datetime.date(2026, 8, 4), datetime.date(2026, 8, 5)]
    assert len(fake.calls) == 2


async def test_probe_returns_empty_when_nothing_found_in_window():
    fake = _FakeVendorClient(set())
    client = _client_with_fake(fake)
    expiries = await client._probe_expiries("SPY", _TODAY, window_days=5)
    assert expiries == []
    assert len(fake.calls) == 5


async def test_list_expiries_falls_back_when_unfiltered_call_is_empty():
    # Unlike the tests above, list_expiries() probes forward from the real
    # `date.today()`, so this fixture's expiry has to be relative. It was
    # hardcoded to 2026-08-05, which rotted into a permanent failure the day
    # it went past — a test that always fails stops being read at all.
    tomorrow = datetime.date.today() + datetime.timedelta(days=1)
    fake = _FakeVendorClient({tomorrow.isoformat()})
    client = _client_with_fake(fake)
    expiries = await client.list_expiries("SPY")
    assert expiries == [tomorrow]
    assert fake.calls[0] is None  # tried the normal path first
