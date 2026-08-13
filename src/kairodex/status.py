"""`kairodex status` — the minimal status page (ARCHITECTURE.md §19 P1 exit
criterion). A CLI text report rather than a dashboard/API endpoint
(docs/PROGRESS.md decision, 2026-08-04) — full dashboards are P6 scope.
"""

from __future__ import annotations

import datetime

from sqlalchemy import func, select, text
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


async def detector_coverage(
    session: AsyncSession, since: datetime.datetime
) -> dict[str, dict[str, int]]:
    """Which detectors actually appear in recorded `signals.evidence`.

    This exists because a registered detector silently returning `None`
    forever is this system's most expensive recurring bug, and nothing
    reported it. Twice now a `build_context` parameter that nothing
    supplied left a whole family dead — `relative_strength` (no
    `index_bars`) and `oi_price_flow` (no `prior_as_of`) — each degrading
    politely to "not applicable" with no warning anywhere. The second one
    took 20,399 signals to notice, and only because `avg_detectors` was
    computed deliberately (PROGRESS.md §16c/§18a).

    Detected by COUNT, not by name: each detector's evidence label lives
    in a module-level constant inside its own module, so a name list here
    would be a second copy free to drift from the real one. The number of
    wired detectors is read straight off the strategy, and a segment
    seeing fewer distinct detectors than that has a dead one — which one
    is obvious from the per-detector counts printed beside it.
    """
    rows = (
        await session.execute(
            text(
                "SELECT s.segment::text AS segment, d->>'detector' AS detector, count(*) AS n "
                "FROM signals s, jsonb_array_elements(s.evidence) d "
                "WHERE s.ts >= :since AND s.evidence IS NOT NULL "
                "GROUP BY 1, 2 ORDER BY 1, 2"
            ),
            {"since": since},
        )
    ).all()
    out: dict[str, dict[str, int]] = {}
    for segment, detector, n in rows:
        out.setdefault(segment, {})[detector] = n
    return out


def _fmt_age(ts: datetime.datetime | None, now: datetime.datetime) -> str:
    if ts is None:
        return "never"
    seconds = (now - ts).total_seconds()
    if seconds < 60:
        return f"{seconds:.0f}s ago"
    if seconds < 3600:
        return f"{seconds / 60:.0f}m ago"
    return f"{seconds / 3600:.1f}h ago"


async def build_report(
    session: AsyncSession, *, wired_detectors: frozenset[str] | None = None
) -> str:
    """`wired_detectors` is passed IN rather than read from
    `kairodex.strategy` here, and that is a layering constraint, not a
    style preference: `kairodex.api.routers.health` calls this function,
    and the "API is glue, not business logic" import contract forbids
    `kairodex.api` from reaching `kairodex.strategy`/`kairodex.engine`.
    Importing the strategy here put the whole engine behind a health
    endpoint and import-linter rejected it, which is the contract doing
    its job. The CLI supplies the set; callers that leave it `None` get
    the per-detector counts without the is-one-dead verdict.
    """
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

    coverage = await detector_coverage(session, since)
    wired = wired_detectors
    lines.append(
        "detectors (24h)" if wired is None else f"detectors (24h, {len(wired)} wired)"
    )
    if not coverage:
        lines.append("  no signals with evidence in the last 24h")
    for segment in sorted(coverage):
        seen = coverage[segment]
        verdict = f"{len(seen)} firing"
        if wired is not None:
            missing = sorted(wired - seen.keys())
            extra = sorted(seen.keys() - wired)
            verdict = f"{len(wired & seen.keys())}/{len(wired)} firing"
            if missing:
                verdict += f"  <<< NEVER FIRED: {', '.join(missing)}"
            if extra:
                # Not an error on its own: a strategy change inside the
                # lookback window leaves the old set in older rows.
                verdict += f"  (unwired, from older rows: {', '.join(extra)})"
        lines.append(f"  {segment}: {verdict}")
        for detector in sorted(seen):
            lines.append(f"      {detector:<22} {seen[detector]:>7} signals")
    lines.append("")
    return "\n".join(lines)
