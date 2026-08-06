"""Export bundle builder — ARCHITECTURE.md §14, "the Claude Code loop."
Pulls one segment's real trades/signals/equity/features for a date window
into a self-describing, versioned directory that an external reviewer (or
Claude Code, reading it back in a fresh session) can trust without
guessing what's real vs. reconstructed:

    manifest.json          schema_version, filters, row counts, sha256 per
                            file, code_sha, active strategy versions, and
                            every file's JSON Schema (embedded, generated
                            from the same pydantic models used to write it)
    trades.jsonl            trade_events.jsonl        signals_rejected.jsonl
    equity.csv               performance.json          feature_dictionary.json
    data_quality.json        digest.md                 README.md

Two-pass write: every content file is written first, then hashed, then
`manifest.json` (which needs those hashes) is written last.
"""

from __future__ import annotations

import csv
import dataclasses
import datetime
import hashlib
import json
import subprocess
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from kairodex.analytics import breakdowns, performance, rollups
from kairodex.analytics import loader as analytics_loader
from kairodex.analytics.types import EquityPoint, PerformanceSummary, TradeRecord
from kairodex.core.enums import Segment
from kairodex.data.quality import QualityFlag
from kairodex.export import digest as digest_mod
from kairodex.export import models as em
from kairodex.features.registry import all_specs
from kairodex.store.models import ChainSnapshot, OptionQuote, Strategy, TradeEvent

_BREAKDOWN_DIMS = ("weekday", "session", "expiry", "moneyness", "vol_regime", "regime")
_GAP_FLAGS = QualityFlag.STALE | QualityFlag.SEQUENCE_GAP


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _code_sha() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True, timeout=5
        )
        return out.stdout.strip()
    except Exception:
        return None  # not a git checkout, or git unavailable — code_sha is best-effort provenance


def _trade_export(t: TradeRecord) -> em.TradeExport:
    return em.TradeExport(
        trade_id=t.trade_id,
        segment=t.segment.value,
        strategy_id=t.strategy_id,
        underlying_symbol=t.underlying_symbol,
        instrument_symbol=t.instrument_symbol,
        option_type=t.option_type,
        strike=t.strike,
        expiry=t.expiry,
        opened_at=t.opened_at,
        closed_at=t.closed_at,
        avg_entry=t.avg_entry,
        avg_exit=t.avg_exit,
        gross_pnl=t.gross_pnl,
        net_pnl=t.net_pnl,
        fees=t.fees,
        r_multiple=t.r_multiple,
        mfe=t.mfe,
        mae=t.mae,
        holding_secs=t.holding_secs,
        exit_reason=t.exit_reason,
        context_entry=t.context_entry,
    )


def _summary_export(s: PerformanceSummary) -> em.PerformanceSummaryExport:
    return em.PerformanceSummaryExport(**dataclasses.asdict(s))


async def _trade_events(session: AsyncSession, trade_ids: list[int]) -> list[em.TradeEventExport]:
    if not trade_ids:
        return []
    rows = await session.scalars(
        select(TradeEvent).where(TradeEvent.trade_id.in_(trade_ids)).order_by(
            TradeEvent.trade_id, TradeEvent.seq
        )
    )
    return [
        em.TradeEventExport(
            event_id=r.event_id, trade_id=r.trade_id, seq=r.seq, ts=r.ts,
            event_type=r.event_type, payload=r.payload,
        )
        for r in rows
    ]


async def _data_quality(
    session: AsyncSession, segment: Segment, frm: datetime.datetime, to: datetime.datetime
) -> em.DataQualityExport:
    provider = "upstox" if segment.market.value == "nse" else "lse"

    async def _rate(prov: str) -> float | None:
        total = await session.scalar(
            select(func.count()).select_from(OptionQuote).where(
                OptionQuote.source == prov, OptionQuote.tier == 1,
                OptionQuote.ts >= frm, OptionQuote.ts < to,
            )
        )
        if not total:
            return None
        flagged = await session.scalar(
            select(func.count()).select_from(OptionQuote).where(
                OptionQuote.source == prov, OptionQuote.tier == 1,
                OptionQuote.ts >= frm, OptionQuote.ts < to,
                OptionQuote.quality.op("&")(int(_GAP_FLAGS)) != 0,
            )
        )
        return (flagged or 0) / total

    gap = await _rate(provider)

    chain_total = await session.scalar(
        select(func.count()).select_from(ChainSnapshot).where(
            ChainSnapshot.ts >= frm, ChainSnapshot.ts < to
        )
    )
    chain_incomplete = await session.scalar(
        select(func.count()).select_from(ChainSnapshot).where(
            ChainSnapshot.ts >= frm, ChainSnapshot.ts < to, ChainSnapshot.complete.is_(False)
        )
    )
    missing_greeks = await session.scalar(
        select(func.count()).select_from(OptionQuote).where(
            OptionQuote.ts >= frm, OptionQuote.ts < to, OptionQuote.iv.is_(None)
        )
    )
    return em.DataQualityExport(
        window_from=frm,
        window_to=to,
        gap_rate_by_provider={provider: gap},
        chain_snapshots_total=chain_total or 0,
        chain_snapshots_incomplete=chain_incomplete or 0,
        option_quotes_missing_own_greeks=missing_greeks or 0,
    )


def _feature_dictionary() -> list[em.FeatureDictEntry]:
    """`fn.__doc__` is the description — every registered feature already
    documents "what it means, how computed" in its own docstring (P2's
    own convention); reusing it here means the export can never describe
    a feature differently than the code that computes it does."""
    return [
        em.FeatureDictEntry(
            name=spec.name,
            description=(spec.fn.__doc__ or "").strip(),
            tier=spec.tier.name,
            fidelity=spec.fidelity.value,
            backtestable=spec.backtestable,
            inputs=list(spec.inputs),
        )
        for spec in all_specs()
    ]


_SCHEMAS = {
    "trade": em.TradeExport.model_json_schema(),
    "trade_event": em.TradeEventExport.model_json_schema(),
    "signal_rejected": em.RejectedSignalExport.model_json_schema(),
    "performance": em.PerformanceExport.model_json_schema(),
    "feature_dictionary_entry": em.FeatureDictEntry.model_json_schema(),
    "data_quality": em.DataQualityExport.model_json_schema(),
}


async def build_bundle(
    session: AsyncSession,
    *,
    segment: Segment,
    frm: datetime.datetime,
    to: datetime.datetime,
    out_dir: Path,
) -> Path:
    """Writes the full bundle under `out_dir` (created if missing) and
    returns the path to `manifest.json`. `out_dir` should already be the
    bundle's own directory (`kairodex export`'s CLI names it
    `bundle_<segment>_<window>/`), not its parent."""
    out_dir.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.datetime.now(datetime.UTC)

    trades = await analytics_loader.load_trades(session, segment, frm=frm, to=to)
    trade_events = await _trade_events(session, [t.trade_id for t in trades])
    rejected = await analytics_loader.load_rejected_signals(session, segment, frm=frm, to=to)
    equity_points = await analytics_loader.load_equity_curve(session, segment, frm=frm, to=to)
    strategies = list(
        await session.scalars(select(Strategy).where(Strategy.segment == segment))
    )
    data_quality = await _data_quality(session, segment, frm, to)

    overall = performance.summarize(trades)
    breakdown_results = {dim: breakdowns.breakdown(trades, dim) for dim in _BREAKDOWN_DIMS}
    equity_stats = performance.equity_curve_stats(equity_points)
    daily_rollup = rollups.rollup(equity_points, "daily")
    perf_export = em.PerformanceExport(
        overall=_summary_export(overall),
        breakdowns={
            dim: {key: _summary_export(s) for key, s in groups.items()}
            for dim, groups in breakdown_results.items()
        },
        equity_curve=em.EquityCurveExport(**dataclasses.asdict(equity_stats)),
        equity_rollup_daily=[
            em.RollupPointExport(**dataclasses.asdict(p)) for p in daily_rollup
        ],
    )
    feature_dict = _feature_dictionary()

    written: list[tuple[str, int | None]] = []

    trades_path = out_dir / "trades.jsonl"
    trades_path.write_text(
        "\n".join(_trade_export(t).model_dump_json() for t in trades) + ("\n" if trades else "")
    )
    written.append(("trades.jsonl", len(trades)))

    events_path = out_dir / "trade_events.jsonl"
    events_path.write_text(
        "\n".join(e.model_dump_json() for e in trade_events) + ("\n" if trade_events else "")
    )
    written.append(("trade_events.jsonl", len(trade_events)))

    rejected_models = [em.RejectedSignalExport(**r) for r in rejected]
    rejected_path = out_dir / "signals_rejected.jsonl"
    rejected_path.write_text(
        "\n".join(r.model_dump_json() for r in rejected_models)
        + ("\n" if rejected_models else "")
    )
    written.append(("signals_rejected.jsonl", len(rejected_models)))

    equity_path = out_dir / "equity.csv"
    _write_equity_csv(equity_path, equity_points)
    written.append(("equity.csv", len(equity_points)))

    performance_path = out_dir / "performance.json"
    performance_path.write_text(perf_export.model_dump_json(indent=2))
    written.append(("performance.json", None))

    feature_dict_path = out_dir / "feature_dictionary.json"
    feature_dict_path.write_text(
        json.dumps([f.model_dump(mode="json") for f in feature_dict], indent=2)
    )
    written.append(("feature_dictionary.json", len(feature_dict)))

    quality_path = out_dir / "data_quality.json"
    quality_path.write_text(data_quality.model_dump_json(indent=2))
    written.append(("data_quality.json", None))

    digest_text = digest_mod.build_digest(
        segment=segment, frm=frm, to=to, overall=overall, breakdown_results=breakdown_results,
        equity_stats=equity_stats, data_quality=data_quality, n_rejected=len(rejected),
    )
    digest_path = out_dir / "digest.md"
    digest_path.write_text(digest_text)
    written.append(("digest.md", None))

    readme_path = out_dir / "README.md"
    readme_path.write_text(_README_TEXT)
    written.append(("README.md", None))

    files = [
        em.ManifestFile(name=name, sha256=_sha256_file(out_dir / name), row_count=count)
        for name, count in written
    ]
    manifest = em.Manifest(
        segment=segment.value,
        window_from=frm,
        window_to=to,
        generated_at=generated_at,
        code_sha=_code_sha(),
        strategy_versions=[
            em.StrategyVersionRef(
                strategy_id=s.strategy_id, name=s.name, version=s.version, status=s.status.value
            )
            for s in strategies
        ],
        files=files,
        schemas=_SCHEMAS,
    )
    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(manifest.model_dump_json(indent=2))
    return manifest_path


def _write_equity_csv(path: Path, points: list[EquityPoint]) -> None:
    with path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["ts", "equity", "high_water_mark", "drawdown", "exposure"])
        for p in points:
            writer.writerow([p.ts.isoformat(), p.equity, p.high_water_mark, p.drawdown, p.exposure])


_README_TEXT = """# Kairodex export bundle

Generated by `kairodex export` (ARCHITECTURE.md §14). Read `digest.md`
first — it's the token-budgeted summary meant to be pasted straight into
a fresh Claude Code session. Everything else here is the full detail it
summarizes.

## Files

- `manifest.json` — schema_version, the exact window/filters used, a
  sha256 of every other file in this bundle (so a copy can be verified
  byte-for-byte later), the code revision that produced it, which
  strategy versions were active, and the JSON Schema each `.jsonl`/
  `.json` file's rows are shaped by.
- `trades.jsonl` — one closed-or-open paper trade per line, fully
  denormalized (no join needed to read it).
- `trade_events.jsonl` — the full lifecycle event log for every trade in
  this window (ARCHITECTURE.md Principle 2 — this is the source of truth
  every other row here is a projection of).
- `signals_rejected.jsonl` — every signal the risk gate chain declined,
  with the stage and reason. Rejections are training data, not noise.
- `equity.csv` — the segment's own equity curve (cash/unrealized/
  realized/exposure/drawdown) over the window.
- `performance.json` — win rate, profit factor, expectancy, R-multiple,
  every breakdown dimension (weekday/session/expiry/moneyness/
  vol_regime/regime), and the equity curve summary + daily rollup.
- `feature_dictionary.json` — what each registered feature means, how
  it's computed, its tier, and whether it's EXACT/PROXY/ESTIMATE — read
  this before drawing conclusions from any feature value elsewhere in
  the bundle; a PROXY or ESTIMATE feature is not ground truth.
- `data_quality.json` — feed gap rate, incomplete chain snapshots, and
  quotes missing our own (vs. vendor) Greeks, all scoped to this exact
  window.
- `digest.md` — the human/LLM-readable summary.

## Known caveats

- `context_entry`/`r_multiple`/`mfe`/`mae` on a trade reflect what the
  live engine actually observed at the time (feature values, position
  marks) — not recomputed after the fact.
- A trade's `r_multiple` is a pure price ratio,
  `(avg_exit - avg_entry) / (avg_entry - initial_stop_price)` — unaffected
  by partial exits changing quantity over the position's life, but `None`
  for any trade opened before this field existed (no `initial_stop_price`
  recorded).
- `session`/`regime`/`vol_regime` breakdown buckets use approximate,
  DST-naive session windows and simple threshold rules — see
  `kairodex.analytics.breakdowns`' own docstrings for the exact rule.
"""
