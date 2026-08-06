"""Daily/weekly/monthly equity rollups from `list[EquityPoint]` — §14's
`equity.csv` export and (eventually) a dashboard sparkline. Pure, DB-free
— buckets an already-fetched point series, doesn't query anything itself.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Literal

from kairodex.analytics.types import EquityPoint, RollupPoint

Granularity = Literal["daily", "weekly", "monthly"]


def _period_key(point: EquityPoint, granularity: Granularity) -> str:
    d = point.ts.date()
    if granularity == "daily":
        return d.isoformat()
    if granularity == "weekly":
        iso = d.isocalendar()
        return f"{iso.year}-W{iso.week:02d}"
    return f"{d.year:04d}-{d.month:02d}"


def rollup(points: list[EquityPoint], granularity: Granularity) -> list[RollupPoint]:
    if not points:
        return []
    ordered = sorted(points, key=lambda p: p.ts)
    buckets: dict[str, list[EquityPoint]] = defaultdict(list)
    for p in ordered:
        buckets[_period_key(p, granularity)].append(p)

    out: list[RollupPoint] = []
    for period in sorted(buckets):
        group = buckets[period]
        open_eq = group[0].equity
        close_eq = group[-1].equity
        high_eq = max(p.equity for p in group)
        low_eq = min(p.equity for p in group)
        max_dd = max(p.drawdown for p in group)
        return_pct = (close_eq - open_eq) / open_eq if open_eq > 0 else Decimal(0)
        out.append(
            RollupPoint(
                period=period,
                open_equity=open_eq,
                close_equity=close_eq,
                high_equity=high_eq,
                low_equity=low_eq,
                max_drawdown_pct=max_dd,
                return_pct=return_pct,
            )
        )
    return out
