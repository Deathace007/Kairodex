"""Upstox V3 WebSocket market-data feed (protobuf).

Schema and wire protocol are the vendor's own — fetched verbatim from
github.com/upstox/upstox-python (see proto/MarketDataFeedV3.proto header) —
not guessed from docs prose. That's a deliberate reaction to the LSE
field-name mistake in docs/PROGRESS.md §6 gotcha #2: the same class of bug
here would poison every option quote recorded, not just one bad test.

Still **unverified against a live message** (no credentials/market hours
available while writing this) — the wire format (authorize handshake,
subscribe JSON, FeedResponse decode) matches the vendor's published example
verbatim, but field *values* should be spot-checked on first live run per
docs/PROGRESS.md's established "verify against live data" step.

Protocol, from the vendor's own example (upstox-python
examples/websocket/market_data/v3/websocket_client.py):
  1. GET https://api.upstox.com/v3/feed/market-data-feed/authorize
     (Bearer auth) -> {"data": {"authorized_redirect_uri": "wss://..."}}
     (one-time-use URL).
  2. Connect to that URL with `websockets`.
  3. Send one JSON text frame: {"guid", "method": "sub",
     "data": {"mode", "instrumentKeys": [...]}}.
  4. Every inbound frame is a binary-encoded `FeedResponse` protobuf
     message: `feeds` is a map of instrument_key -> Feed, each Feed holding
     exactly one of `ltpc` / `fullFeed` (marketFF for options, indexFF for
     indices) / `firstLevelWithGreeks`, per the requested mode.
"""

from __future__ import annotations

import datetime
import json
import logging
import uuid
from collections.abc import AsyncIterator
from decimal import Decimal

import httpx
import websockets

from kairodex.data.types import FeedMode, Tick
from kairodex.data.upstox.auth import AnalyticsToken
from kairodex.data.upstox.proto import MarketDataFeedV3_pb2 as pb

logger = logging.getLogger(__name__)

_AUTHORIZE_URL = "https://api.upstox.com/v3/feed/market-data-feed/authorize"

# Both our QUOTE and FULL modes map to Upstox's "full" (full_d5): it's the
# only mode carrying depth *and* greeks together (see MarketFullFeed in the
# .proto), which is what the T1 tier's chain recording actually wants.
# "full_d30" (30-level depth) needs a separate account entitlement we can't
# assume; "ltpc" and "option_greeks" (single-depth-level) stay available for
# callers that explicitly ask for FeedMode.LTP.
_MODE_MAP: dict[FeedMode, str] = {
    FeedMode.LTP: "ltpc",
    FeedMode.QUOTE: "full",
    FeedMode.FULL: "full",
}


async def get_authorized_ws_uri(http: httpx.AsyncClient, token: AnalyticsToken) -> str:
    resp = await http.get(_AUTHORIZE_URL, headers=token.bearer_header)
    resp.raise_for_status()
    body = resp.json()
    uri: str = body["data"]["authorized_redirect_uri"]
    return uri


async def stream(
    http: httpx.AsyncClient, token: AnalyticsToken, keys: list[str], mode: FeedMode
) -> AsyncIterator[Tick]:
    """Connect, subscribe, and yield Ticks until the caller stops iterating
    or the connection drops. One connection per call — reconnect/backoff is
    the caller's job (kairodex.data.recorder), matching how LSE's own
    stream_async separates transport retry from a single session."""
    uri = await get_authorized_ws_uri(http, token)
    wire_mode = _MODE_MAP[mode]

    async with websockets.connect(uri) as ws:
        # Sent as a binary frame, matching the vendor's own example exactly
        # (json.dumps(...).encode("utf-8") before .send()) rather than a
        # text frame — unverified live, so mirror their proven behavior
        # rather than assume the server treats both frame types the same.
        subscribe_msg = json.dumps(
            {
                "guid": str(uuid.uuid4()),
                "method": "sub",
                "data": {"mode": wire_mode, "instrumentKeys": keys},
            }
        ).encode("utf-8")
        await ws.send(subscribe_msg)
        async for message in ws:
            if not isinstance(message, bytes):
                continue  # text frames are protocol chatter, not feed data
            feed_response = pb.FeedResponse()
            feed_response.ParseFromString(message)
            if feed_response.type != pb.live_feed:
                continue
            ts = datetime.datetime.fromtimestamp(
                feed_response.currentTs / 1000, tz=datetime.UTC
            )
            for instrument_key, feed in feed_response.feeds.items():
                tick = _parse_feed(instrument_key, feed, ts)
                if tick is not None:
                    yield tick


def _to_decimal(value: float) -> Decimal | None:
    # Protobuf leaves unset optional-ish scalar fields at their zero value
    # (proto3 has no field presence for these); 0.0 is indistinguishable
    # from "not sent" here, so treat it as absent rather than a real price.
    return Decimal(str(value)) if value else None


def _parse_feed(instrument_key: str, feed: pb.Feed, ts: datetime.datetime) -> Tick | None:
    which = feed.WhichOneof("FeedUnion")

    if which == "ltpc":
        return _tick_from_ltpc(instrument_key, feed.ltpc, ts)

    if which == "fullFeed":
        full = feed.fullFeed
        full_which = full.WhichOneof("FullFeedUnion")
        if full_which == "marketFF":
            return _tick_from_market_full(instrument_key, full.marketFF, ts)
        if full_which == "indexFF":
            return _tick_from_ltpc(instrument_key, full.indexFF.ltpc, ts)
        return None

    if which == "firstLevelWithGreeks":
        return _tick_from_first_level(instrument_key, feed.firstLevelWithGreeks, ts)

    return None


def _tick_from_ltpc(instrument_key: str, ltpc: pb.LTPC, ts: datetime.datetime) -> Tick:
    return Tick(instrument_key=instrument_key, ts=ts, ltp=_to_decimal(ltpc.ltp))


def _tick_from_market_full(
    instrument_key: str, full: pb.MarketFullFeed, ts: datetime.datetime
) -> Tick:
    best = full.marketLevel.bidAskQuote[0] if full.marketLevel.bidAskQuote else None
    greeks = full.optionGreeks
    return Tick(
        instrument_key=instrument_key,
        ts=ts,
        ltp=_to_decimal(full.ltpc.ltp),
        bid=_to_decimal(best.bidP) if best else None,
        ask=_to_decimal(best.askP) if best else None,
        bid_sz=best.bidQ if best else None,
        ask_sz=best.askQ if best else None,
        volume=full.vtt or None,
        oi=int(full.oi) if full.oi else None,
        vendor_iv=_to_decimal(full.iv),
        delta=_to_decimal(greeks.delta),
        gamma=_to_decimal(greeks.gamma),
        theta=_to_decimal(greeks.theta),
        vega=_to_decimal(greeks.vega),
        rho=_to_decimal(greeks.rho),
    )


def _tick_from_first_level(
    instrument_key: str, first: pb.FirstLevelWithGreeks, ts: datetime.datetime
) -> Tick:
    depth = first.firstDepth
    greeks = first.optionGreeks
    return Tick(
        instrument_key=instrument_key,
        ts=ts,
        ltp=_to_decimal(first.ltpc.ltp),
        bid=_to_decimal(depth.bidP),
        ask=_to_decimal(depth.askP),
        bid_sz=depth.bidQ or None,
        ask_sz=depth.askQ or None,
        volume=first.vtt or None,
        oi=int(first.oi) if first.oi else None,
        vendor_iv=_to_decimal(first.iv),
        delta=_to_decimal(greeks.delta),
        gamma=_to_decimal(greeks.gamma),
        theta=_to_decimal(greeks.theta),
        vega=_to_decimal(greeks.vega),
        rho=_to_decimal(greeks.rho),
    )
