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


@app.command("engine")
def engine_cmd(
    segment: Segment = typer.Option(..., help="nse_stock, nse_index, us_stock, or us_index"),
    live: bool = typer.Option(
        False,
        help=(
            "Real paper capital (SimulatedBroker) instead of shadow mode "
            "(ShadowLogger, zero capital, the default). Never real broker "
            "capital — see ARCHITECTURE.md §11, 'live trading is absent code.'"
        ),
    ),
) -> None:
    """The engine process (ARCHITECTURE.md §3): `engine --segment
    {nse_stock,nse_index,us_stock,us_index}`, one per segment. Requires
    `ingest sync-instruments`/`sync-watchlist` to have populated that
    segment's watchlist first, and `ingest run` to be feeding it live
    quotes."""
    from kairodex.engine.live_loop import run_segment

    asyncio.run(run_segment(segment, shadow=not live))


backtest_app = typer.Typer(help="P4: Track A backtesting over underlying OHLCV")
app.add_typer(backtest_app, name="backtest")


@backtest_app.command("fetch-history")
def fetch_history_cmd(
    market: Market = typer.Option(..., help="nse or us"),
    start: str = typer.Option(..., help="YYYY-MM-DD"),
    end: str = typer.Option(..., help="YYYY-MM-DD"),
) -> None:
    """Backfill daily underlying OHLCV (ARCHITECTURE.md §13's data
    source: Upstox candles v3 / LSE vault) for every instrument in
    `market`'s watchlist plus its segments' benchmark indices — run once
    before `backtest run`."""
    start_date = datetime.date.fromisoformat(start)
    end_date = datetime.date.fromisoformat(end)
    asyncio.run(_fetch_history(market, start_date, end_date))


async def _fetch_history(market: Market, start: datetime.date, end: datetime.date) -> None:
    from sqlalchemy import select

    from kairodex.backtest.history import backfill_underlying_history
    from kairodex.data.recorder import watchlist_instruments
    from kairodex.features.loader import benchmark_symbol
    from kairodex.store.models import Instrument

    sessionmaker = get_sessionmaker()
    client, provider = make_client(market)
    exchange = "NSE" if market is Market.NSE else "US"
    try:
        async with sessionmaker() as session:
            instruments: dict[int, Instrument] = {}
            for segment in Segment:
                if segment.market is not market:
                    continue
                for inst in await watchlist_instruments(session, segment):
                    instruments[inst.instrument_id] = inst
                bench = await session.scalar(
                    select(Instrument).where(
                        Instrument.exchange == exchange,
                        Instrument.symbol == benchmark_symbol(segment),
                    )
                )
                if bench is not None:
                    instruments[bench.instrument_id] = bench

            for inst in instruments.values():
                count = await backfill_underlying_history(
                    session, client, provider, inst, start=start, end=end
                )
                typer.echo(f"{inst.symbol}: {count} bars")
    finally:
        await client.aclose()


@backtest_app.command("run")
def backtest_run_cmd(
    segment: Segment = typer.Option(..., help="nse_stock, nse_index, us_stock, or us_index"),
    frm: str = typer.Option(..., "--from", help="YYYY-MM-DD"),
    to: str = typer.Option(..., "--to", help="YYYY-MM-DD"),
) -> None:
    """Track A backtest (ARCHITECTURE.md §13) over `segment`'s whole
    watchlist, pooled into one strategy-level result and scored against
    the promotion gate table. Requires `fetch-history` to have populated
    `underlying_bars` for the range first. Enforces the held-out final
    period guard before running anything, and writes one `backtest_runs`
    row per invocation."""
    asyncio.run(
        _backtest_run(segment, datetime.date.fromisoformat(frm), datetime.date.fromisoformat(to))
    )


async def _backtest_run(segment: Segment, frm: datetime.date, to: datetime.date) -> None:
    import dataclasses
    from decimal import Decimal

    from sqlalchemy import func, select

    from kairodex.backtest import metrics as backtest_metrics
    from kairodex.backtest.promotion import evaluate_track_a, summarize
    from kairodex.backtest.runner import run_backtest
    from kairodex.backtest.validation import (
        assert_not_touching_holdout,
        deflated_sharpe,
        walk_forward_splits,
    )
    from kairodex.core.clock import LiveClock
    from kairodex.data.recorder import watchlist_instruments
    from kairodex.store.models import BacktestRun
    from kairodex.store.models import Strategy as StrategyRow
    from kairodex.strategy.protocol import ReferenceStrategy
    from kairodex.strategy.scorer import ConfluenceScorer

    start_dt = datetime.datetime.combine(frm, datetime.time.min, tzinfo=datetime.UTC)
    end_dt = datetime.datetime.combine(to, datetime.time.min, tzinfo=datetime.UTC)
    now = LiveClock().now()
    assert_not_touching_holdout(end_dt, now=now)  # raises before touching the DB at all

    sessionmaker = get_sessionmaker()
    strategy = ReferenceStrategy()
    scorer = ConfluenceScorer()
    async with sessionmaker() as session:
        row = await session.scalar(
            select(StrategyRow).where(
                StrategyRow.segment == segment, StrategyRow.name == strategy.id
            )
        )
        if row is None:
            typer.echo(
                "no strategies row for this segment/strategy yet — "
                "run `kairodex engine --segment ...` at least once first (it creates one)."
            )
            raise typer.Exit(1)

        underlyings = await watchlist_instruments(session, segment)
        all_signals = []
        for u in underlyings:
            sigs = await run_backtest(
                session, segment=segment, underlying=u, strategy=strategy, scorer=scorer,
                start=start_dt, end=end_dt,
            )
            all_signals.extend(sigs)
            typer.echo(f"{u.symbol}: {len(sigs)} resolved signals")

        embargo = datetime.timedelta(days=10)
        folds = walk_forward_splits(all_signals, n_folds=4, embargo=embargo)
        prior_runs = await session.scalar(
            select(func.count())
            .select_from(BacktestRun)
            .where(BacktestRun.strategy_id == row.strategy_id)
        )
        n_trials = (prior_runs or 0) + 1
        result = evaluate_track_a(all_signals, folds, n_trials=n_trials)
        m = backtest_metrics.compute_metrics(all_signals)
        returns = [s.outcome.return_atr for s in all_signals if s.outcome is not None]
        dsr = deflated_sharpe(returns, n_trials=n_trials)

        typer.echo(summarize(result.checks))
        typer.echo(f"\noverall: {'VALIDATED-READY' if result.passed else 'not ready'}")

        run = BacktestRun(
            created_at=now,
            segment=segment,
            strategy_id=row.strategy_id,
            from_ts=start_dt,
            to_ts=end_dt,
            config={"n_folds": 4, "embargo_days": 10},
            metrics=dataclasses.asdict(m),
            trial_count=n_trials,
            deflated_sharpe=Decimal(str(dsr)) if dsr is not None else None,
        )
        session.add(run)
        await session.commit()
        typer.echo(f"backtest_runs row {run.run_id} written")


@app.command("export")
def export_cmd(
    segment: Segment = typer.Option(..., help="nse_stock, nse_index, us_stock, or us_index"),
    frm: str = typer.Option(..., "--from", help="YYYY-MM-DD"),
    to: str = typer.Option(..., "--to", help="YYYY-MM-DD"),
    out: str = typer.Option("data/exports", help="Parent directory for the bundle folder"),
) -> None:
    """P5: build a self-describing export bundle (ARCHITECTURE.md §14) for
    one segment/window — manifest, trades, trade_events, rejected signals,
    equity, performance + breakdowns, feature dictionary, data quality,
    and a digest.md meant to be pasted into a fresh Claude Code session."""
    asyncio.run(
        _export(segment, datetime.date.fromisoformat(frm), datetime.date.fromisoformat(to), out)
    )


async def _export(segment: Segment, frm: datetime.date, to: datetime.date, out: str) -> None:
    from pathlib import Path

    from kairodex.export.bundle import build_bundle

    frm_dt = datetime.datetime.combine(frm, datetime.time.min, tzinfo=datetime.UTC)
    to_dt = datetime.datetime.combine(to, datetime.time.min, tzinfo=datetime.UTC)
    out_dir = Path(out) / f"bundle_{segment.value}_{frm.isoformat()}_{to.isoformat()}"

    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        manifest_path = await build_bundle(
            session, segment=segment, frm=frm_dt, to=to_dt, out_dir=out_dir
        )
    typer.echo(f"bundle written to {out_dir} ({manifest_path.name})")


research_app = typer.Typer(help="P5: the export/review loop's return path")
app.add_typer(research_app, name="research")


@research_app.command("import-notes")
def import_notes_cmd(
    path: str = typer.Argument(..., help="Path to a findings.json produced by a bundle review"),
) -> None:
    """ARCHITECTURE.md §14: "Return path: `kairodex research import-notes
    findings.json` -> `research_notes`, linked to the strategy versions it
    causes. The loop closes and stays auditable.\""""
    asyncio.run(_import_notes(path))


async def _import_notes(path: str) -> None:
    from pathlib import Path

    from kairodex.export.research_import import import_notes

    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        note = await import_notes(session, Path(path))
    typer.echo(f"research_notes row {note.note_id} written (status={note.status})")


analytics_app = typer.Typer(help="P5: performance metrics and breakdowns")
app.add_typer(analytics_app, name="analytics")


@analytics_app.command("report")
def analytics_report_cmd(
    segment: Segment = typer.Option(..., help="nse_stock, nse_index, us_stock, or us_index"),
    frm: str = typer.Option(None, "--from", help="YYYY-MM-DD, defaults to 30 days ago"),
    to: str = typer.Option(None, "--to", help="YYYY-MM-DD, defaults to now"),
) -> None:
    """Text performance summary for one segment — the CLI-first live
    verification path for P5, same role `kairodex status`/`backtest run`
    played for P1/P4."""
    asyncio.run(_analytics_report(segment, frm, to))


async def _analytics_report(segment: Segment, frm: str | None, to: str | None) -> None:
    from kairodex.analytics import breakdowns, performance
    from kairodex.analytics import loader as analytics_loader

    to_dt = (
        datetime.datetime.fromisoformat(to).replace(tzinfo=datetime.UTC)
        if to
        else datetime.datetime.now(datetime.UTC)
    )
    frm_dt = (
        datetime.datetime.fromisoformat(frm).replace(tzinfo=datetime.UTC)
        if frm
        else to_dt - datetime.timedelta(days=30)
    )

    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        trades = await analytics_loader.load_trades(session, segment, frm=frm_dt, to=to_dt)
        equity = await analytics_loader.load_equity_curve(session, segment, frm=frm_dt, to=to_dt)

    summary = performance.summarize(trades)
    eq_stats = performance.equity_curve_stats(equity)
    typer.echo(f"{segment.value}  {frm_dt.date()} to {to_dt.date()}")
    typer.echo(
        f"trades: {summary.n_trades} ({summary.n_open} open, {summary.n_closed} closed)  "
        f"win_rate={summary.win_rate}  profit_factor={summary.profit_factor}  "
        f"avg_r={summary.avg_r_multiple}  net_pnl={summary.net_pnl}"
    )
    typer.echo(
        f"equity: current={eq_stats.current_equity}  hwm={eq_stats.high_water_mark}  "
        f"max_drawdown={eq_stats.max_drawdown_pct}  total_return={eq_stats.total_return_pct}"
    )
    for dim in ("weekday", "session", "expiry", "moneyness", "vol_regime", "regime"):
        groups = breakdowns.breakdown(trades, dim)
        if groups:
            typer.echo(f"by {dim}: " + ", ".join(f"{k}={v.n_trades}" for k, v in groups.items()))


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
