"""Pydantic export-bundle schemas (ARCHITECTURE.md §14). Defined once and
used for both jobs: `bundle.py` serializes real data through these same
models (so what's written can never silently drift from the shape it
claims), and `Model.model_json_schema()` emits the JSON Schema embedded
in `manifest.json` — no hand-maintained parallel `.schema.json` file that
could say something different from what the code actually writes.

`Decimal` fields serialize to strings (pydantic's own default for
`Decimal` in JSON mode) rather than floats — matches this codebase's
`numeric(18,4)`/"never float" money convention (ARCHITECTURE.md §5) all
the way out to the exported bundle.
"""

from __future__ import annotations

import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel


class TradeExport(BaseModel):
    trade_id: int
    segment: str
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
    gross_pnl: Decimal | None
    net_pnl: Decimal | None
    fees: Decimal | None
    r_multiple: float | None
    mfe: Decimal | None
    mae: Decimal | None
    holding_secs: int | None
    exit_reason: str | None
    context_entry: dict[str, Any] | None


class TradeEventExport(BaseModel):
    event_id: int
    trade_id: int
    seq: int
    ts: datetime.datetime
    event_type: str
    payload: dict[str, Any]


class RejectedSignalExport(BaseModel):
    signal_id: int
    ts: datetime.datetime
    underlying_id: int
    direction: str
    confidence: float
    reject_stage: str | None
    reject_reason: str | None
    evidence: Any | None


class PerformanceSummaryExport(BaseModel):
    n_trades: int
    n_open: int
    n_closed: int
    win_rate: float | None
    profit_factor: float | None
    expectancy: Decimal | None
    avg_r_multiple: float | None
    gross_pnl: Decimal
    net_pnl: Decimal
    total_fees: Decimal
    avg_win: Decimal | None
    avg_loss: Decimal | None
    avg_holding_secs: float | None


class EquityCurveExport(BaseModel):
    n_points: int
    start_equity: Decimal | None
    current_equity: Decimal | None
    high_water_mark: Decimal | None
    max_drawdown_pct: Decimal | None
    total_return_pct: Decimal | None


class RollupPointExport(BaseModel):
    period: str
    open_equity: Decimal
    close_equity: Decimal
    high_equity: Decimal
    low_equity: Decimal
    max_drawdown_pct: Decimal
    return_pct: Decimal


class PerformanceExport(BaseModel):
    overall: PerformanceSummaryExport
    breakdowns: dict[str, dict[str, PerformanceSummaryExport]]
    equity_curve: EquityCurveExport
    equity_rollup_daily: list[RollupPointExport]


class FeatureDictEntry(BaseModel):
    name: str
    description: str
    tier: str
    fidelity: str
    backtestable: dict[str, bool]
    inputs: list[str]


class DataQualityExport(BaseModel):
    window_from: datetime.datetime
    window_to: datetime.datetime
    gap_rate_by_provider: dict[str, float | None]
    chain_snapshots_total: int
    chain_snapshots_incomplete: int
    option_quotes_missing_own_greeks: int


class ManifestFile(BaseModel):
    name: str
    sha256: str
    row_count: int | None = None


class StrategyVersionRef(BaseModel):
    strategy_id: int
    name: str
    version: int
    status: str


class Manifest(BaseModel):
    schema_version: str = "1"
    segment: str
    window_from: datetime.datetime
    window_to: datetime.datetime
    generated_at: datetime.datetime
    code_sha: str | None
    strategy_versions: list[StrategyVersionRef]
    files: list[ManifestFile]
    schemas: dict[str, dict[str, Any]]
