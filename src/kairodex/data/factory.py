"""Vendor client construction, shared by every entrypoint that needs a live
MarketDataProvider — the CLI's one-shot commands and P1's recorder loop.
Kept out of kairodex.cli so kairodex.data.recorder doesn't have to import
the CLI module to build a client."""

from __future__ import annotations

from kairodex.config import get_settings
from kairodex.core.enums import Market
from kairodex.data.ports import MarketDataProvider


def make_client(market: Market) -> tuple[MarketDataProvider, str]:
    settings = get_settings()
    if market is Market.NSE:
        from kairodex.data.upstox.auth import AnalyticsToken
        from kairodex.data.upstox.client import UpstoxClient

        token = AnalyticsToken(settings.upstox_access_token, settings.upstox_token_expires_at)
        return UpstoxClient(token), "upstox"

    from kairodex.data.lse.client import LSEClient

    return LSEClient(settings.lse_api_key), "lse"
