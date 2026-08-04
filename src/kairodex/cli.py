"""Kairodex CLI entrypoint. Every process in ARCHITECTURE.md §3 runs through
this (`kairodex ingest`, `engine`, `api`, `jobs`, `research`); P0 only wires
up enough of `ingest` to prove the vendor adapters work end to end against
a live database."""

from __future__ import annotations

import asyncio
import datetime

import typer

from kairodex.core.enums import InstrumentKind, Market, Segment
from kairodex.data.factory import make_client
from kairodex.data.ingest import store_chain_snapshot
from kairodex.data.types import InstrumentRecord
from kairodex.store.base import get_sessionmaker

app = typer.Typer(help="AI-assisted options-buying paper trading platform")
ingest_app = typer.Typer(help="Market data ingestion")
app.add_typer(ingest_app, name="ingest")

# ADR 0007: LSE carries no true index instruments (SPX/NDX/RUT return zero
# contracts) — the US_INDEX segment trades these index-tracking ETF options
# instead. Keep in sync with config/watchlist.yaml's us_index list; this
# constant only classifies segment for the one-shot pull-chain debug
# command below, the recorder's real classification comes from the
# watchlist (kairodex/data/recorder.py).
_US_INDEX_PROXY_SYMBOLS = {"SPY", "QQQ", "DIA", "IWM"}


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
    sessionmaker = get_sessionmaker()
    client, provider = make_client(market)

    if market is Market.NSE:
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
        currency = "USD"
        is_index_symbol = underlying.upper() in _US_INDEX_PROXY_SYMBOLS
        segment = Segment.US_INDEX if is_index_symbol else Segment.US_STOCK
        underlying_rec = InstrumentRecord(
            exchange="US",
            symbol=underlying.upper(),
            # Always UNDERLYING, never INDEX — these are real ETF shares,
            # not index values (ADR 0007). `segment` (US_INDEX vs US_STOCK)
            # is the product classification; `kind` describes what the
            # instrument technically is, and the two aren't the same axis.
            kind=InstrumentKind.UNDERLYING,
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


@ingest_app.command("sync-instruments")
def sync_instruments_cmd(market: Market = typer.Option(..., help="nse or us")) -> None:
    """T0: pull the full instrument master for a market into `instruments`.

    Run this before `sync-watchlist` — watchlist seeding matches against
    whatever is already in the table.
    """
    asyncio.run(_sync_instruments(market))


async def _sync_instruments(market: Market) -> None:
    from kairodex.data.sync import sync_instruments

    sessionmaker = get_sessionmaker()
    client, provider = make_client(market)
    try:
        async with sessionmaker() as session:
            count = await sync_instruments(session, client, provider)
        typer.echo(f"Synced {count} {market.value} instruments from {provider}.")
    finally:
        await client.aclose()


@ingest_app.command("sync-watchlist")
def sync_watchlist_cmd(
    market: Market = typer.Option(..., help="nse or us"),
    watchlist_file: str = typer.Option("config/watchlist.yaml", help="Path to watchlist YAML"),
) -> None:
    """T1: seed `watchlist_membership` from config/watchlist.yaml.

    Requires `sync-instruments` to have run for this market first.
    """
    asyncio.run(_sync_watchlist(market, watchlist_file))


async def _sync_watchlist(market: Market, watchlist_file: str) -> None:
    import yaml

    from kairodex.data.sync import sync_watchlist

    with open(watchlist_file) as f:
        config = yaml.safe_load(f)

    sessionmaker = get_sessionmaker()
    segments = [s for s in Segment if s.market is market]
    async with sessionmaker() as session:
        for segment in segments:
            symbols = config.get(segment.value, [])
            if not symbols:
                continue
            matched, missed = await sync_watchlist(session, segment, symbols)
            typer.echo(f"{segment.value}: {len(matched)} matched, {len(missed)} missed {missed}")


@ingest_app.command("run")
def run_cmd(market: Market = typer.Option(..., help="nse or us")) -> None:
    """The P1 recorder: long-lived T1 REST poll + WS stream for one market.

    Matches ARCHITECTURE.md §3's `ingest --market {nse,us}` process — run one
    of these per market, restart-policy `always`. Requires `sync-instruments`
    and `sync-watchlist` to have populated the watchlist first.
    """
    from kairodex.data.recorder import run_market

    asyncio.run(run_market(market))


@app.command("status")
def status_cmd() -> None:
    """Minimal status page (ARCHITECTURE.md §19 P1 exit criterion): per-market
    connection state, last message age, gap rate, from `feed_health` /
    `option_quotes`."""
    asyncio.run(_status())


async def _status() -> None:
    from kairodex.status import build_report

    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        typer.echo(await build_report(session))


@app.command("jobs")
def jobs_cmd() -> None:
    """The periodic-checks process (ARCHITECTURE.md §3) — currently just the
    annual Upstox token-expiry check."""
    from kairodex.jobs import run

    run()


if __name__ == "__main__":
    app()
