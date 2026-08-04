"""LSE (London Strategic Edge) adapter — implements MarketDataProvider for US.

Thin async wrapper around the vendor's own `lse-data` PyPI package rather
than a hand-rolled REST client: it already handles auth, retries, and the
vault's async-export job protocol for bulk history — reimplementing that
would be strictly more code for a worse result (the ladder, rung 5).

The vendor client is synchronous (urllib-based REST, though it has native
async streaming); each call here runs it in a thread via asyncio.to_thread
so it behaves like the rest of this async codebase.

Chain-row field names below are confirmed against a real authenticated
`options()` call (2026-08-04): ticker, underlying, strike, expiry,
contract_type ("call"/"put" — not "type"), last_price, volume_today (not
"volume"), premium_today, underlying_price, dte, iv, delta, gamma, theta,
vega, rho, last_trade_at, updated_at. Notably: **no bid/ask/open-interest
fields exist on this endpoint** — the docs mentioning a "chain with bid/ask"
were wrong for this vendor; those Tick fields stay None for LSE quotes.

Every entry in `options_underlyings()` is a real, tradable security (stock
or ETF) — this vendor's options catalog carries no true INDEX-kind
instruments at all (confirmed live, ADR 0007: SPX/NDX/RUT return zero
contracts, not an error, they're simply not offered). `instruments()`
below always yields `InstrumentKind.UNDERLYING`; which trading segment
(US_STOCK vs. US_INDEX) an underlying belongs to is a watchlist/product
classification (`config/watchlist.yaml`, ADR 0007), not something derivable
from the vendor's own data.
"""

from __future__ import annotations

import asyncio
import datetime
from collections.abc import AsyncIterator
from typing import Any

from lse import LSE, LSEError, OptionTick

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
            yield InstrumentRecord(
                exchange="US",
                symbol=row["symbol"].upper(),
                kind=InstrumentKind.UNDERLYING,
                currency="USD",
                provider_ids={"lse": row["symbol"].upper()},
            )

    async def subscribe(self, keys: list[str], mode: FeedMode) -> AsyncIterator[Tick]:
        """Stream option ticks for the given underlyings (`keys` — e.g.
        ["AAPL", "TSLA"], not individual contract keys: `subscribe_options`
        subscribes to every strike/expiry for an underlying in one call).

        LSE's feed has no mode selection (ltp/quote/full) — `mode` is
        accepted only to satisfy MarketDataProvider's shared signature.
        """
        self._client.subscribe_options(keys)
        async for lse_tick in self._client.stream_async([], reconnect=True):
            tick = _parse_stream_tick(lse_tick)
            if tick is not None:
                yield tick

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

    async def list_expiries(self, underlying: str) -> list[datetime.date]:
        # options() with no expiry filter returns every listed expiry's
        # contracts at once (confirmed against the installed client's
        # signature: `options(underlying, type=None, expiry=None, ...)`).
        try:
            rows = await asyncio.to_thread(self._client.options, underlying)
        except LSEError as e:
            raise VendorError(f"LSE options() failed: [{e.status}] {e.message}") from e
        today = datetime.date.today()
        expiries: set[datetime.date] = set()
        for row in rows:
            raw = row.get("expiry")
            if not raw:
                continue
            expiry = datetime.date.fromisoformat(str(raw)[:10])
            if expiry >= today:
                expiries.add(expiry)
        return sorted(expiries)

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


def _parse_stream_tick(lse_tick: object) -> Tick | None:
    """lse.Tick/OptionTick (from stream_async, subscribed via
    subscribe_options) -> our Tick. Confirmed against the installed
    lse-data==0.14.0 source (lse/client.py): OptionTick.from_symbol parses
    the OSI contract symbol into strike/right/expiry; the base Tick carries
    price/bid/ask/volume/timestamp regardless."""
    price = getattr(lse_tick, "price", None)
    if price is None:
        return None
    symbol = getattr(lse_tick, "symbol", "") or ""
    ts = getattr(lse_tick, "datetime", None) or to_utc(datetime.datetime.now(datetime.UTC))
    bid = getattr(lse_tick, "bid", None)
    ask = getattr(lse_tick, "ask", None)
    volume = getattr(lse_tick, "volume", None)

    option_type = None
    strike = None
    if isinstance(lse_tick, OptionTick):
        option_type = {"call": "C", "put": "P"}.get(lse_tick.right)
        strike = to_decimal(lse_tick.strike)

    return Tick(
        instrument_key=symbol,
        ts=ts,
        strike=strike,
        option_type=option_type,
        ltp=to_decimal(price),
        bid=to_decimal(bid) if bid is not None else None,
        ask=to_decimal(ask) if ask is not None else None,
        volume=int(volume) if volume is not None else None,
    )


def _parse_chain_row(row: dict[str, Any], ts: datetime.datetime) -> Tick:
    otype = row.get("contract_type")
    return Tick(
        instrument_key=row.get("ticker") or "",
        ts=ts,
        strike=to_decimal(row.get("strike")),
        option_type={"call": "C", "put": "P"}.get(str(otype).lower()) if otype else None,
        ltp=to_decimal(row.get("last_price")),
        # No bid/ask/open-interest on this endpoint — left None (see module docstring).
        volume=row.get("volume_today"),
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
