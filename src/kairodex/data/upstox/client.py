"""Upstox adapter — implements MarketDataProvider for NSE.

Endpoints (verified against live responses / current docs, not assumed):
  - Instrument master: static gzip JSON, no auth, refreshed ~06:00 IST daily.
    https://assets.upstox.com/market-quote/instruments/exchange/{exchange}.json.gz
  - Option chain: GET /v2/option/chain?instrument_key=&expiry_date=YYYY-MM-DD
  - Historical candles v3: GET /v3/historical-candle/{key}/{unit}/{interval}/{to}/{from}
  - Rate limits: 50 req/s, 500 req/min, 2000 req/30min for all market-data
    endpoints (kairodex.data.upstox.ratelimit).

Live WebSocket streaming (protobuf feed) is P1 (the recorder) scope, not
P0 — `subscribe()` is intentionally unimplemented until then.
"""

from __future__ import annotations

import datetime
import gzip
import json
from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential_jitter

from kairodex.core.enums import ExerciseStyle, InstrumentKind, Market, Segment, Settlement
from kairodex.core.errors import VendorError
from kairodex.core.tz import exchange_tz
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
from kairodex.data.upstox.auth import AnalyticsToken
from kairodex.data.upstox.ratelimit import RateLimiter

_NSE_TZ = exchange_tz(Market.NSE)

_BASE_URL = "https://api.upstox.com/v2"
_BARS_BASE_URL = "https://api.upstox.com/v3"
_INSTRUMENTS_URL = "https://assets.upstox.com/market-quote/instruments/exchange/{exchange}.json.gz"

# Only the underlying/derivative kinds this platform trades or prices against
# (ARCHITECTURE.md §8 needs futures for the synthetic-forward Greeks input).
_WANTED_EQ_TYPES = {"EQ"}
_WANTED_FO_TYPES = {"CE", "PE", "FUT"}

_TIMEFRAME_TO_V3 = {
    Timeframe.ONE_MIN: ("minutes", "1"),
    Timeframe.THREE_MIN: ("minutes", "3"),
    Timeframe.FIVE_MIN: ("minutes", "5"),
    Timeframe.FIFTEEN_MIN: ("minutes", "15"),
    Timeframe.THIRTY_MIN: ("minutes", "30"),
    Timeframe.ONE_HOUR: ("hours", "1"),
    Timeframe.ONE_DAY: ("days", "1"),
}

_retry_transient = retry(
    reraise=True,
    stop=stop_after_attempt(4),
    wait=wait_exponential_jitter(initial=0.5, max=8),
    retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
)


class UpstoxClient:
    def __init__(self, token: AnalyticsToken, http: httpx.AsyncClient | None = None) -> None:
        self._token = token
        self._http = http or httpx.AsyncClient(timeout=15.0)
        self._limiter = RateLimiter()

    async def aclose(self) -> None:
        await self._http.aclose()

    @_retry_transient
    async def _get(self, url: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        await self._limiter.acquire()
        resp = await self._http.get(url, params=params, headers=self._token.bearer_header)
        if resp.status_code == 429:
            raise VendorError(f"Upstox rate limit hit despite local limiter: {resp.text}")
        resp.raise_for_status()
        result: dict[str, Any] = resp.json()
        return result

    # --- MarketDataProvider ---------------------------------------------

    async def instruments(self, exchange: str = "NSE") -> AsyncIterator[InstrumentRecord]:
        await self._limiter.acquire()
        resp = await self._http.get(_INSTRUMENTS_URL.format(exchange=exchange))
        resp.raise_for_status()
        records = json.loads(gzip.decompress(resp.content))

        for rec in records:
            parsed = _parse_instrument(rec)
            if parsed is not None:
                yield parsed

    async def subscribe(self, keys: list[str], mode: FeedMode) -> AsyncIterator[Tick]:
        # ponytail: WS protobuf feed is P1 (the recorder) work — nothing in
        # P0 needs a live stream, only chain()/bars(). Raising loudly rather
        # than shipping a fake stream that silently yields nothing.
        raise NotImplementedError("Upstox WS streaming lands with the P1 recorder")
        yield  # pragma: no cover - makes this an async generator for typing

    async def chain(self, underlying: str, expiry: datetime.date) -> ChainSnapshot:
        data = await self._get(
            f"{_BASE_URL}/option/chain",
            params={"instrument_key": underlying, "expiry_date": expiry.isoformat()},
        )
        rows = data.get("data", [])
        quotes: list[Tick] = []
        now = to_utc(datetime.datetime.now(datetime.UTC))
        for row in rows:
            underlying_px = to_decimal(row.get("underlying_spot_price"))
            strike = to_decimal(row.get("strike_price"))
            for side, otype in (("call_options", "C"), ("put_options", "P")):
                leg = row.get(side)
                if leg:
                    quotes.append(_parse_leg_quote(leg, underlying_px, strike, otype, now))
        return ChainSnapshot(underlying=underlying, expiry=expiry, ts=now, quotes=quotes)

    async def bars(
        self, key: str, tf: Timeframe, start: datetime.date, end: datetime.date
    ) -> list[Bar]:
        unit, interval = _TIMEFRAME_TO_V3[tf]
        data = await self._get(
            f"{_BARS_BASE_URL}/historical-candle/{key}/{unit}/{interval}/"
            f"{end.isoformat()}/{start.isoformat()}"
        )
        candles = data.get("data", {}).get("candles", [])
        return [_parse_candle(c) for c in candles]

    async def quota(self) -> QuotaStatus:
        # Upstox publishes fixed rate-limit constants rather than exposing a
        # usage-query endpoint (unlike LSE) — this reports our own tracked
        # consumption against those documented ceilings.
        return QuotaStatus(used_pct=self._limiter.used_pct())


def _parse_instrument(rec: dict[str, Any]) -> InstrumentRecord | None:
    segment = rec.get("segment")
    itype = rec.get("instrument_type")

    if segment == "NSE_EQ" and itype in _WANTED_EQ_TYPES:
        return InstrumentRecord(
            exchange="NSE",
            symbol=rec["trading_symbol"],
            kind=InstrumentKind.UNDERLYING,
            currency="INR",
            provider_ids={"upstox": rec["instrument_key"]},
            lot_size=int(rec.get("lot_size", 1)),
            tick_size=to_decimal(rec.get("tick_size")),
        )

    if segment == "NSE_INDEX" and itype == "INDEX":
        return InstrumentRecord(
            exchange="NSE",
            symbol=rec["trading_symbol"],
            kind=InstrumentKind.INDEX,
            currency="INR",
            provider_ids={"upstox": rec["instrument_key"]},
        )

    if segment == "NSE_FO" and itype in _WANTED_FO_TYPES:
        # Underlyings carry no segment themselves (ARCHITECTURE.md §5.1); an
        # option/future's segment is derived from what it's written on.
        is_index_underlying = rec.get("underlying_type") == "INDEX"
        inst_segment = Segment.NSE_INDEX if is_index_underlying else Segment.NSE_STOCK
        expiry_date = _epoch_ms_to_ist_date(rec["expiry"]) if rec.get("expiry") else None
        kind = InstrumentKind.FUTURE if itype == "FUT" else InstrumentKind.OPTION
        return InstrumentRecord(
            exchange="NSE",
            symbol=rec["trading_symbol"],
            kind=kind,
            currency="INR",
            provider_ids={"upstox": rec["instrument_key"]},
            segment=inst_segment,
            underlying_symbol=rec.get("underlying_symbol"),
            strike=to_decimal(rec.get("strike_price")) if itype in ("CE", "PE") else None,
            option_type={"CE": "C", "PE": "P"}.get(itype),
            expiry=expiry_date,
            exercise_style=ExerciseStyle.EUROPEAN if itype in ("CE", "PE") else None,
            settlement=(
                Settlement.CASH
                if is_index_underlying
                else (Settlement.PHYSICAL if itype in ("CE", "PE") else None)
            ),
            lot_size=int(rec.get("lot_size", 1)),
            tick_size=to_decimal(rec.get("tick_size")),
        )

    return None


def _epoch_ms_to_ist_date(epoch_ms: int) -> datetime.date:
    """Upstox expiry timestamps are epoch millis for the contract's last
    trading instant (~15:29:59 IST) — the calendar date must be read in the
    exchange's own timezone, not the UTC date, even though the two agree for
    NSE's market hours today."""
    dt = datetime.datetime.fromtimestamp(epoch_ms / 1000, tz=datetime.UTC)
    return dt.astimezone(_NSE_TZ).date()


def _parse_leg_quote(
    leg: dict[str, Any],
    underlying_px: Decimal | None,
    strike: Decimal | None,
    option_type: str,
    ts: datetime.datetime,
) -> Tick:
    md = leg.get("market_data", {})
    greeks = leg.get("option_greeks", {})
    return Tick(
        instrument_key=leg["instrument_key"],
        ts=ts,
        strike=strike,
        option_type=option_type,
        ltp=to_decimal(md.get("ltp")),
        bid=to_decimal(md.get("bid_price")),
        ask=to_decimal(md.get("ask_price")),
        bid_sz=md.get("bid_qty"),
        ask_sz=md.get("ask_qty"),
        volume=md.get("volume"),
        oi=md.get("oi"),
        oi_change=(md.get("oi") - md.get("prev_oi")) if md.get("prev_oi") is not None else None,
        underlying_px=underlying_px,
        vendor_iv=to_decimal(greeks.get("iv")),
        delta=to_decimal(greeks.get("delta")),
        gamma=to_decimal(greeks.get("gamma")),
        theta=to_decimal(greeks.get("theta")),
        vega=to_decimal(greeks.get("vega")),
    )


def _parse_candle(row: list[Any]) -> Bar:
    ts = to_utc(datetime.datetime.fromisoformat(str(row[0])))
    o, h, low_, c = (to_decimal(v) for v in row[1:5])
    if o is None or h is None or low_ is None or c is None:
        raise VendorError(f"malformed candle from Upstox (missing OHLC): {row!r}")
    return Bar(
        ts=ts,
        open=o,
        high=h,
        low=low_,
        close=c,
        volume=int(row[5]),
        oi=int(row[6]) if len(row) > 6 and row[6] is not None else None,
    )
