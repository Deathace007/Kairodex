"""Token-budgeted digest.md — ARCHITECTURE.md §14: "paste this into
Claude Code." A few KB of prose + tables, not a dump of every row (that's
what the other bundle files are for) — the point is a reviewer (human or
Claude Code in a fresh session) gets the shape of the window in one read,
then goes to `trades.jsonl`/`performance.json` for anything specific.
"""

from __future__ import annotations

import datetime
from decimal import Decimal

from kairodex.analytics.types import EquityCurveStats, PerformanceSummary
from kairodex.core.enums import Segment
from kairodex.export.models import DataQualityExport

_MAX_ROWS_PER_BREAKDOWN = 8  # ponytail: a bounded table, not every bucket ever seen


def _pct(x: float | None) -> str:
    return f"{x:.1%}" if x is not None else "n/a"


def _num(x: object) -> str:
    return f"{x:.4g}" if isinstance(x, int | float) else (str(x) if x is not None else "n/a")


def _opt(x: object) -> str:
    return str(x) if x is not None else "n/a"


def _pct_opt(x: Decimal | None) -> str:
    return _pct(float(x)) if x is not None else "n/a"


def _summary_line(label: str, s: PerformanceSummary) -> str:
    return (
        f"| {label} | {s.n_trades} | {_pct(s.win_rate)} | {_num(s.profit_factor)} "
        f"| {_num(s.avg_r_multiple)} | {s.net_pnl} |"
    )


def build_digest(
    *,
    segment: Segment,
    frm: datetime.datetime,
    to: datetime.datetime,
    overall: PerformanceSummary,
    breakdown_results: dict[str, dict[str, PerformanceSummary]],
    equity_stats: EquityCurveStats,
    data_quality: DataQualityExport,
    n_rejected: int,
) -> str:
    lines = [
        f"# Kairodex export digest — {segment.value}",
        "",
        f"Window: {frm.date()} to {to.date()} "
        f"({(to - frm).days} days). Generated "
        f"{datetime.datetime.now(datetime.UTC).strftime('%Y-%m-%d %H:%M UTC')}.",
        "",
        "## Overall performance",
        "",
        "| | trades | win rate | profit factor | avg R | net P&L |",
        "|---|---|---|---|---|---|",
        _summary_line("all", overall),
        "",
        f"{overall.n_open} still open, {overall.n_closed} closed. "
        f"{n_rejected} signals rejected in this window (see `signals_rejected.jsonl`).",
        "",
        "## Equity",
        "",
        f"- Start: {_opt(equity_stats.start_equity)}",
        f"- Current: {_opt(equity_stats.current_equity)}",
        f"- High water mark: {_opt(equity_stats.high_water_mark)}",
        f"- Max drawdown: {_pct_opt(equity_stats.max_drawdown_pct)}",
        f"- Total return: {_pct_opt(equity_stats.total_return_pct)}",
        "",
        "## Breakdowns",
    ]
    for dim, groups in breakdown_results.items():
        if not groups:
            continue
        lines += [
            "",
            f"### by {dim}",
            "",
            "| bucket | trades | win rate | profit factor | avg R | net P&L |",
            "|---|---|---|---|---|---|",
        ]
        ranked = sorted(groups.items(), key=lambda kv: kv[1].n_trades, reverse=True)
        for key, s in ranked[:_MAX_ROWS_PER_BREAKDOWN]:
            lines.append(_summary_line(key, s))

    lines += [
        "",
        "## Data quality",
        "",
        f"- Gap rate: {data_quality.gap_rate_by_provider}",
        f"- Chain snapshots: {data_quality.chain_snapshots_total} total, "
        f"{data_quality.chain_snapshots_incomplete} incomplete",
        f"- Option quotes missing our own Greeks (vendor-only): "
        f"{data_quality.option_quotes_missing_own_greeks}",
        "",
        "## Caveats",
        "",
        "- See `README.md` for what each file means and known limitations.",
        "- `feature_dictionary.json` labels every feature EXACT/PROXY/ESTIMATE — "
        "check before trusting a feature value as ground truth.",
    ]
    return "\n".join(lines)
