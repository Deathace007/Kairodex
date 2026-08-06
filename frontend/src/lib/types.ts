// Mirrors the shapes kairodex/api/routers/*.py actually return. Kept as
// plain interfaces (not generated from the OpenAPI schema) — the API
// surface is small and stable enough that hand-matching is cheaper than
// a codegen step, per ARCHITECTURE.md §16's own "thin" framing for this
// frontend.

export type Segment = "nse_stock" | "nse_index" | "us_stock" | "us_index";

export const SEGMENTS: Segment[] = ["nse_stock", "nse_index", "us_stock", "us_index"];

/** A Python `Decimal` field, as it actually arrives over the wire — a
 * JSON *string* (FastAPI's precision-preserving choice, matching this
 * codebase's "money is numeric(18,4), never float" convention all the
 * way out to the API), not a `number`. `float`/`int`-sourced fields
 * (win_rate, confidence, counts) stay plain `number`. Always render a
 * `Decimal` through `lib/format.ts`'s `fmt*` helpers, which coerce both
 * shapes — see that file's own note. */
export type Decimal = string;

export interface PerformanceSummary {
  n_trades: number;
  n_open: number;
  n_closed: number;
  win_rate: number | null;
  profit_factor: number | null;
  expectancy: Decimal | null;
  avg_r_multiple: number | null;
  gross_pnl: Decimal;
  net_pnl: Decimal;
  total_fees: Decimal;
  avg_win: Decimal | null;
  avg_loss: Decimal | null;
  avg_holding_secs: number | null;
}

export interface EquityCurveStats {
  n_points: number;
  start_equity: Decimal | null;
  current_equity: Decimal | null;
  high_water_mark: Decimal | null;
  max_drawdown_pct: Decimal | null;
  total_return_pct: Decimal | null;
}

export interface SegmentOverview {
  segment: Segment;
  open_positions: number;
  signals_24h: number;
  performance_7d: PerformanceSummary;
  equity: EquityCurveStats;
  breaker_status: string;
}

export interface EquityPoint {
  ts: string;
  equity: Decimal;
  high_water_mark: Decimal;
  drawdown: Decimal;
  exposure: Decimal;
}

export interface Position {
  trade_id: number;
  instrument_symbol: string | null;
  opened_at: string;
  qty_lots: number;
  avg_entry: Decimal;
  mark: Decimal | null;
  unrealized: Decimal | null;
  greeks: {
    iv: Decimal | null;
    delta: Decimal | null;
    gamma: Decimal | null;
    theta: Decimal | null;
    vega: Decimal | null;
  } | null;
  // Current (possibly-ratcheted) stop — monitor.py's trailing-stop logic
  // can move this up over the trade's life.
  stop_price: Decimal | null;
  profit_target: Decimal | null;
}

export interface Opportunity {
  signal_id: number;
  ts: string;
  underlying_symbol: string | null;
  direction: "buy" | "sell";
  confidence: number;
  decision: string;
  reject_stage: string | null;
  reject_reason: string | null;
  evidence: { detector: string; family: string; score: number; weight: number }[] | null;
}

export interface TradeRow {
  trade_id: number;
  segment: Segment;
  strategy_id: number;
  underlying_symbol: string;
  instrument_symbol: string;
  option_type: string | null;
  strike: Decimal | null;
  expiry: string | null;
  opened_at: string;
  closed_at: string | null;
  avg_entry: Decimal;
  avg_exit: Decimal | null;
  initial_stop_price: Decimal | null;
  profit_target: Decimal | null;
  gross_pnl: Decimal | null;
  net_pnl: Decimal | null;
  fees: Decimal | null;
  r_multiple: number | null;
  mfe: Decimal | null;
  mae: Decimal | null;
  holding_secs: number | null;
  exit_reason: string | null;
}

export interface TradesPage {
  total: number;
  page: number;
  page_size: number;
  trades: TradeRow[];
}

export interface RiskSummary {
  segment: Segment;
  config: Record<string, number | string>;
  daily_pnl: Decimal | null;
  weekly_pnl: Decimal | null;
  consecutive_losses: number;
  breaker_status: string;
  breaker_reason: string | null;
  blocked_until: string | null;
  risk_multiplier: Decimal | null;
}

export interface FeedHealthRow {
  provider: string;
  connected: boolean;
  last_message_age_secs: number | null;
  subscribed_count: number;
  quota_used_pct: number | null;
  clock_skew_ms: number | null;
  gap_rate_24h: number | null;
  last_error: string | null;
  last_error_at: string | null;
}

export interface MasterSegmentRow {
  segment: Segment;
  currency: string;
  equity: Decimal | null;
  equity_converted: Decimal | null;
  high_water_mark: Decimal | null;
  max_drawdown_pct: Decimal | null;
  breaker_status: string;
}

export interface ResearchNoteRow {
  note_id: number;
  created_at: string;
  segment: Segment | null;
  status: string;
  findings: unknown;
}

export interface MasterOverview {
  ccy: string;
  segments: MasterSegmentRow[];
  feed_health: { provider: string; connected: boolean }[];
  research_insights: ResearchNoteRow[];
}

export interface StrategyRow {
  strategy_id: number;
  segment: Segment;
  name: string;
  version: number;
  status: string;
}

export const SEGMENT_LABEL: Record<Segment, string> = {
  nse_stock: "NSE Stock",
  nse_index: "NSE Index",
  us_stock: "US Stock",
  us_index: "US Index",
};
