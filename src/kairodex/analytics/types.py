"""Shared, DB-free types for `kairodex.analytics` — the flattened shape
`loader.py` builds from `trades`/`instruments`/`position_marks`, so every
pure metric function downstream (performance/breakdowns/rollups) never
needs its own join or its own opinion about what a "trade" looks like.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from decimal import Decimal

from kairodex.core.enums import Segment


@dataclass(frozen=True, slots=True)
class TradeRecord:
    """One paper trade (open or closed), flattened for analytics.

    `initial_stop_price` comes from `Trade.risk_params["initial_stop_price"]`
    (engine/orchestrator.py's own bookkeeping) — needed to express R-multiple
    as a pure price ratio, `(exit - entry) / (entry - initial_stop)`, without
    needing the original (pre-partial-exit) quantity anywhere (this is an
    options-*buying*-only platform — every trade is a long, ARCHITECTURE.md
    §5.4/`core.enums.Side`'s own docstring — so this ratio's sign is always
    meaningful the same way).

    `profit_target` comes from the same `risk_params` blob (`Trade.
    risk_params["profit_target"]`, `_DEFAULT_PROFIT_TARGET_PCT` in
    `engine.orchestrator`) — the take-profit level set at fill time, for
    display (dashboards showing "what SL/TP was this trade opened with").
    Unlike `initial_stop_price`, the live stop can ratchet up over a
    trade's life (`monitor.py`'s trailing-stop logic) — this is always
    the *entry-time* value, never the current one; the open-positions API
    endpoint reads the current (possibly-ratcheted) stop separately,
    straight off `risk_params["stop_price"]`, for exactly that reason.

    `mfe`/`mae` are the best/worst *unrealized* P&L (currency units) ever
    marked for this trade, from `position_marks.unrealized` — the column's
    own docstring says it "powers MFE/MAE," computed here at query time
    rather than written back onto `trades` by the engine, so a live trade's
    MFE/MAE is always exactly what open-position monitoring actually saw,
    not a running approximation. `None` for a trade with no marks yet.
    """

    trade_id: int
    segment: Segment
    strategy_id: int
    underlying_symbol: str
    instrument_symbol: str
    option_type: str | None
    strike: Decimal | None
    expiry: datetime.date | None
    opened_at: datetime.datetime
    closed_at: datetime.datetime | None
    avg_entry: Decimal
    avg_exit: Decimal | None
    initial_stop_price: Decimal | None
    profit_target: Decimal | None
    gross_pnl: Decimal | None
    net_pnl: Decimal | None
    fees: Decimal | None
    holding_secs: int | None
    exit_reason: str | None
    context_entry: dict[str, object] | None
    mfe: Decimal | None
    mae: Decimal | None

    @property
    def is_closed(self) -> bool:
        return self.closed_at is not None

    @property
    def r_multiple(self) -> float | None:
        """`(avg_exit - avg_entry) / (avg_entry - initial_stop_price)` — a
        pure price ratio, so it's unaffected by partial exits changing
        the position's quantity over its life. `None` if unclosed, or if
        `initial_stop_price` was never recorded (pre-P5 trades, or a
        degenerate zero-risk stop)."""
        if self.avg_exit is None or self.initial_stop_price is None:
            return None
        risk = self.avg_entry - self.initial_stop_price
        if risk == 0:
            return None
        return float((self.avg_exit - self.avg_entry) / risk)

    @property
    def underlying_px_entry(self) -> float | None:
        if not self.context_entry:
            return None
        val = self.context_entry.get("underlying_px")
        return float(val) if isinstance(val, int | float) else None

    @property
    def vol_regime_entry(self) -> float | None:
        if not self.context_entry:
            return None
        val = self.context_entry.get("vol_regime")
        return float(val) if isinstance(val, int | float) else None

    @property
    def trend_state_entry(self) -> float | None:
        if not self.context_entry:
            return None
        val = self.context_entry.get("trend_state_strength")
        return float(val) if isinstance(val, int | float) else None


@dataclass(frozen=True, slots=True)
class PerformanceSummary:
    n_trades: int
    n_open: int
    n_closed: int
    win_rate: float | None
    profit_factor: float | None
    expectancy: Decimal | None  # mean net_pnl per closed trade
    avg_r_multiple: float | None
    gross_pnl: Decimal
    net_pnl: Decimal
    total_fees: Decimal
    avg_win: Decimal | None
    avg_loss: Decimal | None
    avg_holding_secs: float | None


@dataclass(frozen=True, slots=True)
class EquityPoint:
    ts: datetime.datetime
    equity: Decimal
    high_water_mark: Decimal
    drawdown: Decimal  # fraction, 0..1 (risk.accounting's own convention)
    exposure: Decimal


@dataclass(frozen=True, slots=True)
class EquityCurveStats:
    n_points: int
    start_equity: Decimal | None
    current_equity: Decimal | None
    high_water_mark: Decimal | None
    max_drawdown_pct: Decimal | None
    total_return_pct: Decimal | None


@dataclass(frozen=True, slots=True)
class RollupPoint:
    period: str  # "2026-08-05" | "2026-W32" | "2026-08"
    open_equity: Decimal
    close_equity: Decimal
    high_equity: Decimal
    low_equity: Decimal
    max_drawdown_pct: Decimal
    return_pct: Decimal
