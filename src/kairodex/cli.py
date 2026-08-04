"""Kairodex CLI entrypoint. Every process in ARCHITECTURE.md §3 runs through
this (`kairodex ingest`, `engine`, `api`, `jobs`, `research`); P0 only wires
up enough of `ingest` to prove the vendor adapters work end to end against
a live database."""

from __future__ import annotations

import asyncio
import datetime

import typer

from kairodex.config import get_settings
from kairodex.core.enums import InstrumentKind, Market, Segment
from kairodex.data.ingest import store_chain_snapshot
from kairodex.data.ports import MarketDataProvider
from kairodex.data.types import InstrumentRecord
from kairodex.store.base import get_sessionmaker

app = typer.Typer(help="AI-assisted options-buying paper trading platform")
ingest_app = typer.Typer(help="Market data ingestion")
app.add_typer(ingest_app, name="ingest")


@ingest_app.command("pull-chain")
def pull_chain(
    market: Market = typer.Option(..., help="nse or us"),
    underlying: str = typer.Option(
        ..., help="Vendor instrument key (Upstox: 'NSE_INDEX|Nifty 50'; LSE: 'AAPL')"
    ),
    expiry: str = typer.Option(..., help="Expiry date, YYYY-MM-DD"),
) -> None:
    """Pull one live option chain and write it into TimescaleDB.

    This is the P0 exit criterion (ARCHITECTURE.md §19): proof that each
    vendor adapter authenticates, fetches real data, and lands it in the
    database through the same upsert path P1's recorder will build on.
    """
    asyncio.run(_pull_chain(market, underlying, datetime.date.fromisoformat(expiry)))


async def _pull_chain(market: Market, underlying: str, expiry: datetime.date) -> None:
    settings = get_settings()
    sessionmaker = get_sessionmaker()

    if market is Market.NSE:
        from kairodex.data.upstox.auth import AnalyticsToken
        from kairodex.data.upstox.client import UpstoxClient

        token = AnalyticsToken(settings.upstox_access_token, settings.upstox_token_expires_at)
        client: MarketDataProvider = UpstoxClient(token)
        provider = "upstox"
        underlying_symbol = underlying.split("|")[-1]
        currency = "INR"
        is_index = "INDEX" in underlying
        segment = Segment.NSE_INDEX if is_index else Segment.NSE_STOCK
        underlying_rec = InstrumentRecord(
            exchange="NSE",
            symbol=underlying_symbol,
            kind=InstrumentKind.INDEX if is_index else InstrumentKind.UNDERLYING,
            currency=currency,
            provider_ids={provider: underlying},
        )
    else:
        from kairodex.data.lse.client import LSEClient

        client = LSEClient(settings.lse_api_key)
        provider = "lse"
        currency = "USD"
        is_index_symbol = underlying.upper() in {"SPX", "NDX", "RUT"}
        segment = Segment.US_INDEX if is_index_symbol else Segment.US_STOCK
        underlying_rec = InstrumentRecord(
            exchange="US",
            symbol=underlying.upper(),
            kind=InstrumentKind.INDEX if segment is Segment.US_INDEX else InstrumentKind.UNDERLYING,
            currency=currency,
            provider_ids={provider: underlying.upper()},
        )

    try:
        typer.echo(f"Fetching {market.value} chain: {underlying} @ {expiry} ...")
        snapshot = await client.chain(underlying, expiry)
        typer.echo(f"Got {snapshot.contract_count} contracts (complete={snapshot.complete})")

        async with sessionmaker() as session:
            snap_id = await store_chain_snapshot(
                session, provider, snapshot, underlying_rec, segment
            )
        typer.echo(f"Stored chain_snapshot {snap_id} with {snapshot.contract_count} quotes.")
    finally:
        await client.aclose()


if __name__ == "__main__":
    app()
