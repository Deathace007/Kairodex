"""LSE (London Strategic Edge) adapter — implements MarketDataProvider for US.

Thin async wrapper around the vendor's own `lse-data` PyPI package rather
than a hand-rolled REST client: it already handles auth, retries, and the
vault's async-export job protocol for bulk history — reimplementing that
would be strictly more code for a worse result (the ladder, rung 5).

The vendor client is synchronous (urllib-based REST, though it has native
async streaming); each call here runs it in a thread via asyncio.to_thread
so it behaves like the rest of this async codebase.

Field-name confidence, since this was built by reading the client's source
(github.com/londonstrategicedge/lse-data) rather than a live authenticated
call — no test key was available while writing this:
  - CONFIRMED (present in lse.client._OPTION_ROUND / candles()/Tick):
    last_price, underlying_price, iv, delta, gamma, theta, vega, rho,
    volume, premium_today, open/high/low/close, timestamp.
  - BEST-EFFORT (plausible names, not seen in a real response): the OSI
    ticker field on a chain row, bid/ask, open interest. Verify these
    against one real `options()` call once UPSTOX_ACCESS_TOKEN's LSE
    counterpart (LSE_API_KEY) is available, and fix the `.get()` keys in
    _parse_chain_row below if the vault uses different names.
"""

from __future__ import annotations

import asyncio
import datetime
from collections.abc import AsyncIterator
from typing import Any

from lse import LSE, LSEError

from kairodex.core.enums import InstrumentKind
from kairodex.core.errors import AuthError, VendorError
from kairodex.data.normalize import to_decimal, to_utc
from kairodex.data.types import (
    Bar,
    ChainSnapshot,
    FeedMode,
    InstrumentRecord,
    QuotaStatus,
    Tick,
    Timeframe,
)

# SPEC_REVIEW.md B1: the US Index segment is SPX/NDX/RUT (European, cash-
# settled); SPY/QQQ/IWM and everything else live in US Stock instead.
_US_INDEX_SYMBOLS = {"SPX", "NDX", "RUT"}


class LSEClient:
    def __init__(self, api_key: str | None) -> None:
        if not api_key:
            raise AuthError(
                "LSE_API_KEY is not set. Get one at https://londonstrategicedge.com/data."
            )
        self._client = LSE(api_key=api_key)

    async def aclose(self) -> None:
        await asyncio.to_thread(self._client.disconnect)

    # --- MarketDataProvider ---------------------------------------------

    async def instruments(self) -> AsyncIterator[InstrumentRecord]:
        rows = await asyncio.to_thread(self._client.options_underlyings)
        for row in rows:
            symbol = row["symbol"].upper()
            is_index = symbol in _US_INDEX_SYMBOLS
            kind = InstrumentKind.INDEX if is_index else InstrumentKind.UNDERLYING
            yield InstrumentRecord(
                exchange="US",
                symbol=symbol,
                kind=kind,
                currency="USD",
                provider_ids={"lse": symbol},
            )

    async def subscribe(self, keys: list[str], mode: FeedMode) -> AsyncIterator[Tick]:
        # ponytail: live streaming wiring is P1 (the recorder) scope, same as
        # the Upstox adapter — P0 only needs chain()/bars(). The vendor client
        # already supports this (stream_async/subscribe_options) when P1 needs it.
        raise NotImplementedError("LSE streaming lands with the P1 recorder")
        yield  # pragma: no cover - makes this an async generator for typing

    async def chain(self, underlying: str, expiry: datetime.date) -> ChainSnapshot:
        try:
            rows = await asyncio.to_thread(
                self._client.options, underlying, expiry=expiry.isoformat()
            )
        except LSEError as e:
            raise VendorError(f"LSE options() failed: [{e.status}] {e.message}") from e

        now = to_utc(datetime.datetime.now(datetime.UTC))
        quotes = [_parse_chain_row(row, now) for row in rows]
        return ChainSnapshot(underlying=underlying, expiry=expiry, ts=now, quotes=quotes)

    async def bars(
        self, key: str, tf: Timeframe, start: datetime.date, end: datetime.date
    ) -> list[Bar]:
        try:
            rows = await asyncio.to_thread(
                self._client.candles,
                key,
                tf.value,  # our Timeframe values ("1m","1h","1d",...) match LSE's directly
                start.isoformat(),
                end.isoformat(),
            )
        except LSEError as e:
            raise VendorError(f"LSE candles() failed: [{e.status}] {e.message}") from e
        return [_parse_candle_row(row) for row in rows]

    async def quota(self) -> QuotaStatus:
        # Not exposed as a wrapped method on the installed client (v0.14.0)
        # despite the vendor's own README mentioning GET /vault/usage — call
        # the client's internal vault helper directly rather than reimplementing
        # auth+HTTP for one endpoint. Degrades to "unknown" if it 404s on a
        # future/past API version instead of breaking ingestion over a
        # non-critical check.
        try:
            raw: dict[str, Any] = await asyncio.to_thread(self._client._vault_call, "/usage")
        except LSEError:
            return QuotaStatus(used_pct=0.0, raw={"available": False})
        used = raw.get("used_pct") or raw.get("used")
        return QuotaStatus(used_pct=float(used) if used is not None else 0.0, raw=raw)


def _parse_chain_row(row: dict[str, Any], ts: datetime.datetime) -> Tick:
    otype = row.get("type")  # best-effort field name — see module docstring
    return Tick(
        instrument_key=row.get("ticker") or row.get("symbol") or "",
        ts=ts,
        strike=to_decimal(row.get("strike")),
        option_type=({"call": "C", "put": "P"}.get(str(otype).lower()) if otype else None),
        ltp=to_decimal(row.get("last_price")),
        bid=to_decimal(row.get("bid") or row.get("bid_price")),
        ask=to_decimal(row.get("ask") or row.get("ask_price")),
        volume=row.get("volume"),
        oi=row.get("oi") or row.get("open_interest"),
        underlying_px=to_decimal(row.get("underlying_price")),
        vendor_iv=to_decimal(row.get("iv")),
        delta=to_decimal(row.get("delta")),
        gamma=to_decimal(row.get("gamma")),
        theta=to_decimal(row.get("theta")),
        vega=to_decimal(row.get("vega")),
        rho=to_decimal(row.get("rho")),
    )


def _parse_candle_row(row: dict[str, Any]) -> Bar:
    ts_raw = str(row["timestamp"]).replace("Z", "+00:00")
    ts = to_utc(datetime.datetime.fromisoformat(ts_raw))
    o = to_decimal(row["open"])
    h = to_decimal(row["high"])
    low_ = to_decimal(row["low"])
    c = to_decimal(row["close"])
    if o is None or h is None or low_ is None or c is None:
        raise VendorError(f"malformed candle from LSE (missing OHLC): {row!r}")
    return Bar(ts=ts, open=o, high=h, low=low_, close=c, volume=int(row.get("volume") or 0))
