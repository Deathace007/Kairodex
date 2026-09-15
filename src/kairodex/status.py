"""`kairodex status` — the minimal status page (ARCHITECTURE.md §19 P1 exit
criterion). A CLI text report rather than a dashboard/API endpoint
(docs/PROGRESS.md decision, 2026-08-04) — full dashboards are P6 scope.
"""

from __future__ import annotations

import datetime
from collections.abc import Mapping

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from kairodex.core.enums import Market
from kairodex.core.sessions import (
    is_session_open_now,
    local_date_for,
    nse_holidays,
    session_window_utc,
)
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


# Thresholds for `health_checks`. Each one names the failure it would have
# caught (docs/reports/2026-09-15-two-segment-audit-and-fix-plan.html §5).
_MIN_SWEEPS_PER_HOUR = 12  # healthy ~24-40; nine sessions ran at 0-3 (chain-scan bug)
_MAX_LABEL_LAG = datetime.timedelta(days=2)  # labels stalled 33 days unnoticed
_FROZEN_LEGS_ALERT = 0.60  # normal day 35-48% (quiet far strikes); holiday 100%


async def health_checks(session: AsyncSession, now: datetime.datetime) -> list[str]:
    """The conditions that each silently broke the engine for days or weeks.
    Every line is `ok` or starts with `<<<` so it can be grepped."""

    async def one(sql: str, **params: object) -> object:
        return (await session.execute(text(sql), params)).scalar()

    lines = ["health"]
    hour_ago = now - datetime.timedelta(hours=1)

    year = local_date_for(Market.NSE, now).year
    if not any(d.year == year for d in nse_holidays()):
        lines.append(f"  <<< config/nse_holidays.yaml has no dates for {year} — add NSE's list")
    if is_session_open_now(Market.NSE, now):
        open_dt, _ = session_window_utc(Market.NSE, local_date_for(Market.NSE, now))
        if now - open_dt > datetime.timedelta(minutes=10):
            bars_today = await one(
                "SELECT count(*) FROM underlying_bars WHERE timeframe = '1m' AND ts >= :open",
                open=open_dt,
            )
            flag = "" if bars_today else "<<< "
            lines.append(
                f"  {flag}NSE underlying bars today: {bars_today}"
                + ("" if bars_today else " — session open but no bars: exchange shut or feed down")
            )
    day_ago = now - datetime.timedelta(hours=24)

    for event, what in (("EXIT_FAILED", "exits that could not fill"),
                        ("EXIT_FORCED", "mandatory exits forced at a penalty")):
        n = await one(
            "SELECT count(*) FROM trade_events WHERE event_type = :e AND ts >= :since",
            e=event, since=day_ago,
        )
        flag = "<<< " if n else ""
        lines.append(f"  {flag}{event} (24h): {n} — {what}")

    last_label = await one("SELECT max(ts) FROM signals WHERE forward_outcome IS NOT NULL")
    lag_flag = (
        "<<< " if isinstance(last_label, datetime.datetime) and now - last_label > _MAX_LABEL_LAG
        else ""
    )
    lines.append(f"  {lag_flag}newest labelled signal: {last_label}")

    rows = (
        await session.execute(
            text(
                "SELECT fv.segment::text AS segment, count(*) AS vectors, "
                "(SELECT count(*) FROM watchlist_membership w WHERE w.segment = fv.segment "
                " AND w.valid_from <= current_date AND w.valid_to >= current_date) AS names "
                "FROM feature_vectors fv WHERE fv.as_of >= :since GROUP BY fv.segment"
            ),
            {"since": hour_ago},
        )
    ).all()
    for segment, vectors, names in rows:
        sweeps = vectors / names if names else 0
        flag = "<<< " if sweeps < _MIN_SWEEPS_PER_HOUR else ""
        lines.append(f"  {flag}{segment} sweeps (last hour): {sweeps:.0f}")
        dead = (
            await session.execute(
                text(
                    "SELECT q.key FROM feature_vectors fv, jsonb_each_text(fv.quality) q "
                    "WHERE fv.segment::text = :seg AND fv.as_of >= :since "
                    "GROUP BY q.key HAVING bool_and(q.value = 'MISSING') ORDER BY 1"
                ),
                {"seg": segment, "since": hour_ago},
            )
        ).scalars().all()
        if dead:
            lines.append(f"  <<< {segment} features MISSING on 100% (last hour): {', '.join(dead)}")

    frozen = (
        await session.execute(
            text(
                "SELECT count(*) AS legs, count(*) FILTER (WHERE rows >= 3 AND variants = 1) "
                "AS frozen FROM (SELECT instrument_id, count(*) AS rows, "
                "count(DISTINCT (bid, ask, ltp, volume)) AS variants FROM option_quotes "
                "WHERE ts >= :since GROUP BY instrument_id) x"
            ),
            {"since": now - datetime.timedelta(minutes=10)},
        )
    ).one()
    if frozen.legs:
        share = frozen.frozen / frozen.legs
        flag = "<<< " if share > _FROZEN_LEGS_ALERT else ""
        lines.append(
            f"  {flag}option legs with frozen content (10 min): {frozen.frozen}/{frozen.legs} "
            f"({share:.0%}) — ~100% means a shut exchange or a replaying feed"
        )
    lines.append("")
    return lines


async def build_report(
    session: AsyncSession,
    *,
    wired_detectors: frozenset[str] | Mapping[str, frozenset[str]] | None = None,
) -> str:
    """`wired_detectors` is passed IN rather than read from
    `kairodex.strategy` here, and that is a layering constraint, not a
    style preference: `kairodex.api.routers.health` calls this function,
    and the "API is glue, not business logic" import contract forbids
    `kairodex.api` from reaching `kairodex.strategy`/`kairodex.engine`.
    Importing the strategy here put the whole engine behind a health
    endpoint and import-linter rejected it, which is the contract doing
    its job. The CLI supplies the set — or a per-segment mapping, since
    nse_index trades a different detector set (strategy.protocol.
    strategy_for); callers that leave it `None` get the per-detector counts
    without the is-one-dead verdict.
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

    lines += await health_checks(session, now)

    coverage = await detector_coverage(session, since)
    lines.append("detectors (24h)")
    if not coverage:
        lines.append("  no signals with evidence in the last 24h")
    for segment in sorted(coverage):
        seen = coverage[segment]
        wired = (
            wired_detectors.get(segment)
            if isinstance(wired_detectors, Mapping)
            else wired_detectors
        )
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
