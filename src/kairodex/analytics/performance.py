"""Pure performance metrics over `list[TradeRecord]` and
`list[EquityPoint]` — ARCHITECTURE.md §15's `/performance` and
`/equity-curve` endpoints, §14's `performance.json`. No DB, no I/O; same
split as `kairodex.backtest.metrics` (which this deliberately parallels —
that module scores *signals* against synthetic forward outcomes, this one
scores *real trades* against their own realized P&L).
"""

from __future__ import annotations

import statistics
from decimal import Decimal

from kairodex.analytics.types import EquityCurveStats, EquityPoint, PerformanceSummary, TradeRecord


def _closed(trades: list[TradeRecord]) -> list[TradeRecord]:
    return [t for t in trades if t.is_closed]


def win_rate(trades: list[TradeRecord]) -> float | None:
    """Denominator is closed trades *with a recorded `net_pnl`* — same
    population `expectancy`/`avg_win`/`avg_loss` use. A closed trade with
    no `net_pnl` can't happen through `run_exit_tick` (it always sets it),
    but counting it as a non-win in this denominator while every sibling
    metric silently drops it would make `win_rate` alone inconsistent
    with the rest of `PerformanceSummary` for any row written another
    way (a manual DB edit, a future code path)."""
    closed = [t for t in _closed(trades) if t.net_pnl is not None]
    if not closed:
        return None
    wins = sum(1 for t in closed if t.net_pnl is not None and t.net_pnl > 0)
    return wins / len(closed)


def profit_factor(trades: list[TradeRecord]) -> float | None:
    """`sum(winning net_pnl) / abs(sum(losing net_pnl))` — undefined (None)
    with no losses to divide by (including "no closed trades at all")."""
    closed = _closed(trades)
    gains = sum((t.net_pnl for t in closed if t.net_pnl is not None and t.net_pnl > 0), Decimal(0))
    losses = sum((t.net_pnl for t in closed if t.net_pnl is not None and t.net_pnl < 0), Decimal(0))
    if losses == 0:
        return None
    return float(gains / abs(losses))


_MONEY = Decimal("0.0001")  # numeric(18,4) — ARCHITECTURE.md §5's own money precision


def expectancy(trades: list[TradeRecord]) -> Decimal | None:
    closed = [t for t in _closed(trades) if t.net_pnl is not None]
    if not closed:
        return None
    total = sum((t.net_pnl for t in closed if t.net_pnl is not None), Decimal(0))
    # quantize, don't leave Decimal's default ~28-digit division noise
    # (e.g. 100/3 -> "33.33333333333333333333333333") in performance.json.
    return (total / len(closed)).quantize(_MONEY)


def avg_r_multiple(trades: list[TradeRecord]) -> float | None:
    values = [t.r_multiple for t in trades if t.r_multiple is not None]
    if not values:
        return None
    return statistics.mean(values)


def avg_win(trades: list[TradeRecord]) -> Decimal | None:
    wins = [t.net_pnl for t in _closed(trades) if t.net_pnl is not None and t.net_pnl > 0]
    if not wins:
        return None
    return sum(wins, Decimal(0)) / len(wins)


def avg_loss(trades: list[TradeRecord]) -> Decimal | None:
    losses = [t.net_pnl for t in _closed(trades) if t.net_pnl is not None and t.net_pnl < 0]
    if not losses:
        return None
    return sum(losses, Decimal(0)) / len(losses)


def avg_holding_secs(trades: list[TradeRecord]) -> float | None:
    values = [t.holding_secs for t in _closed(trades) if t.holding_secs is not None]
    if not values:
        return None
    return float(statistics.mean(values))  # statistics.mean(list[int]) can return an int


def summarize(trades: list[TradeRecord]) -> PerformanceSummary:
    closed = _closed(trades)
    gross = sum((t.gross_pnl for t in closed if t.gross_pnl is not None), Decimal(0))
    net = sum((t.net_pnl for t in closed if t.net_pnl is not None), Decimal(0))
    fees = sum((t.fees for t in trades if t.fees is not None), Decimal(0))
    return PerformanceSummary(
        n_trades=len(trades),
        n_open=len(trades) - len(closed),
        n_closed=len(closed),
        win_rate=win_rate(trades),
        profit_factor=profit_factor(trades),
        expectancy=expectancy(trades),
        avg_r_multiple=avg_r_multiple(trades),
        gross_pnl=gross,
        net_pnl=net,
        total_fees=fees,
        avg_win=avg_win(trades),
        avg_loss=avg_loss(trades),
        avg_holding_secs=avg_holding_secs(trades),
    )


def equity_curve_stats(points: list[EquityPoint]) -> EquityCurveStats:
    """`max_drawdown_pct` is the worst *observed* drawdown across the
    window (each point's own `drawdown` is already "distance below the
    high-water-mark as of that snapshot," `kairodex.risk.accounting`'s own
    definition) — not recomputed from equity alone, so it stays consistent
    with what the risk engine itself was seeing at the time."""
    if not points:
        return EquityCurveStats(
            n_points=0,
            start_equity=None,
            current_equity=None,
            high_water_mark=None,
            max_drawdown_pct=None,
            total_return_pct=None,
        )
    ordered = sorted(points, key=lambda p: p.ts)
    start = ordered[0].equity
    current = ordered[-1].equity
    hwm = max(p.high_water_mark for p in ordered)
    max_dd = max(p.drawdown for p in ordered)
    total_return = (current - start) / start if start > 0 else None
    return EquityCurveStats(
        n_points=len(ordered),
        start_equity=start,
        current_equity=current,
        high_water_mark=hwm,
        max_drawdown_pct=max_dd,
        total_return_pct=total_return,
    )
