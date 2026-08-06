"""LSE quota handling. Live-discovered 2026-08-06: the T1 poll spent 36h
issuing 28,152 calls that could only ever 429, because a quota rejection
was indistinguishable from any other vendor error and `/usage` reported
0.00% used while the account sat at 100%. Both are behaviours, so both get
tests."""

import datetime

import pytest
from lse import LSEError

from kairodex.core.errors import RateLimitError, VendorError
from kairodex.data.lse.client import LSEClient
from kairodex.data.types import Timeframe

_QUOTA_MSG = "daily request limit reached (15000/day)"


class _FailingVendorClient:
    """Stands in for lse.LSE, raising a chosen LSEError from every endpoint."""

    def __init__(self, status: int, message: str) -> None:
        self._error = LSEError(status, message)
        self.call_count = 0

    def options(self, *args: object, **kwargs: object) -> list[dict]:
        self.call_count += 1
        raise self._error

    def candles(self, *args: object, **kwargs: object) -> list[dict]:
        self.call_count += 1
        raise self._error

    def _vault_call(self, *args: object, **kwargs: object) -> dict:
        self.call_count += 1
        raise self._error


def _client_with_fake(fake: object) -> LSEClient:
    client = LSEClient.__new__(LSEClient)
    client._client = fake  # type: ignore[attr-defined]
    return client


async def test_429_raises_rate_limit_error_not_generic_vendor_error():
    client = _client_with_fake(_FailingVendorClient(429, _QUOTA_MSG))
    with pytest.raises(RateLimitError):
        await client.list_expiries("AAPL")


async def test_non_429_stays_a_plain_vendor_error():
    """The distinction has to cut both ways, or the caller's backoff branch
    swallows ordinary failures and stalls ingestion for 30 minutes over a
    malformed query."""
    client = _client_with_fake(_FailingVendorClient(500, "internal error"))
    with pytest.raises(VendorError) as exc:
        await client.list_expiries("AAPL")
    assert not isinstance(exc.value, RateLimitError)


async def test_chain_and_bars_classify_429_too():
    client = _client_with_fake(_FailingVendorClient(429, _QUOTA_MSG))
    with pytest.raises(RateLimitError):
        await client.chain("AAPL", datetime.date(2026, 8, 14))
    with pytest.raises(RateLimitError):
        await client.bars("AAPL", Timeframe.ONE_MIN, datetime.date.today(), datetime.date.today())


async def test_probe_expiries_aborts_on_429_instead_of_burning_the_window():
    """The probe treats a per-date miss as expected and steps over it. A
    quota rejection is not a miss: swallowing it meant 14 more doomed calls
    per underlying per cycle, which is how the daily cap became a weekly one."""
    fake = _FailingVendorClient(429, _QUOTA_MSG)
    client = _client_with_fake(fake)
    with pytest.raises(RateLimitError):
        await client._probe_expiries("SPY", datetime.date(2026, 8, 6), window_days=14)
    assert fake.call_count == 1  # aborted on the first, not 14 attempts


async def test_usage_429_reports_exhausted_not_idle():
    """`/usage` is itself metered, so it 429s exactly when the account is
    exhausted. Reporting that as 0.0% (the old blanket except) is the one
    reading that guarantees nobody notices."""
    client = _client_with_fake(_FailingVendorClient(429, _QUOTA_MSG))
    assert (await client.quota()).used_pct == 100.0


async def test_usage_other_failure_is_unknown_not_zero():
    client = _client_with_fake(_FailingVendorClient(404, "no such endpoint"))
    assert (await client.quota()).used_pct is None
