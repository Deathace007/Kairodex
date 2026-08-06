"""`kairodex status` — the minimal status page (ARCHITECTURE.md §19 P1 exit
criterion). A CLI text report rather than a dashboard/API endpoint
(docs/PROGRESS.md decision, 2026-08-04) — full dashboards are P6 scope.
"""

from __future__ import annotations

import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from kairodex.data.quality import QualityFlag
from kairodex.store.models import FeedHealth, OptionQuote

_PROVIDER_LABELS = {"upstox": "upstox (NSE)", "lse": "lse (US)"}
# "Gap rate" for the P1 exit criterion (<0.5% on T1): the fraction of T1
# option_quotes rows flagged stale or sequence-gapped in the lookback window.
_GAP_FLAGS = QualityFlag.STALE | QualityFlag.SEQUENCE_GAP


async def gap_rate(
    session: AsyncSession,
    provider: str,
    since: datetime.datetime,
    *,
    until: datetime.datetime | None = None,
) -> float | None:
    """`until=None` (the default, and every existing caller before this
    parameter existed) means "up to now" — unbounded above, exactly the
    original behavior. `kairodex.export.bundle` passes a real `until` so
    a bundle's `data_quality.json` reports the gap rate *within its own
    export window*, not always-up-to-the-current-moment — reusing this
    function rather than a second copy of the same query."""
    stmt = select(func.count()).select_from(OptionQuote).where(
        OptionQuote.source == provider, OptionQuote.tier == 1, OptionQuote.ts >= since
    )
    if until is not None:
        stmt = stmt.where(OptionQuote.ts < until)
    total = await session.scalar(stmt)
    if not total:
        return None
    flagged_stmt = select(func.count()).select_from(OptionQuote).where(
        OptionQuote.source == provider,
        OptionQuote.tier == 1,
        OptionQuote.ts >= since,
        OptionQuote.quality.op("&")(int(_GAP_FLAGS)) != 0,
    )
    if until is not None:
        flagged_stmt = flagged_stmt.where(OptionQuote.ts < until)
    flagged = await session.scalar(flagged_stmt)
    return (flagged or 0) / total


def _fmt_age(ts: datetime.datetime | None, now: datetime.datetime) -> str:
    if ts is None:
        return "never"
    seconds = (now - ts).total_seconds()
    if seconds < 60:
        return f"{seconds:.0f}s ago"
    if seconds < 3600:
        return f"{seconds / 60:.0f}m ago"
    return f"{seconds / 3600:.1f}h ago"


async def build_report(session: AsyncSession) -> str:
    now = datetime.datetime.now(datetime.UTC)
    since = now - datetime.timedelta(hours=24)
    rows = list(await session.scalars(select(FeedHealth)))
    if not rows:
        return "No feed_health rows yet — has `kairodex ingest run` started for any market?"

    lines = [f"kairodex status — {now.strftime('%Y-%m-%d %H:%M UTC')}", ""]
    for row in rows:
        label = _PROVIDER_LABELS.get(row.provider, row.provider)
        rate = await gap_rate(session, row.provider, since)
        rate_str = f"{rate:.2%}" if rate is not None else "n/a (no T1 quotes in 24h)"
        quota = f"{float(row.quota_used_pct):.0f}%" if row.quota_used_pct is not None else "n/a"
        lines += [
            label,
            f"  connected:        {'yes' if row.connected else 'no'}",
            f"  last message:     {_fmt_age(row.last_message_at, now)}",
            f"  subscribed:       {row.subscribed_count} instruments",
            f"  quota used:       {quota}",
            f"  gap rate (24h):   {rate_str}",
        ]
        if row.last_error:
            lines.append(
                f"  last error:       {row.last_error} ({_fmt_age(row.last_error_at, now)})"
            )
        else:
            lines.append("  last error:       none")
        lines.append("")
    return "\n".join(lines)
