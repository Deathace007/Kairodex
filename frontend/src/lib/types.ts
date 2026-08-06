// Mirrors the shapes kairodex/api/routers/*.py actually return. Kept as
// plain interfaces (not generated from the OpenAPI schema) — the API
// surface is small and stable enough that hand-matching is cheaper than
// a codegen step, per ARCHITECTURE.md §16's own "thin" framing for this
// frontend.

export type Segment = "nse_stock" | "nse_index" | "us_stock" | "us_index";

export const SEGMENTS: Segment[] = ["nse_stock", "nse_index", "us_stock", "us_index"];

export interface PerformanceSummary {
  n_trades: number;
  n_open: number;
  n_closed: number;
  win_rate: number | null;
  profit_factor: number | null;
  expectancy: number | null;
  avg_r_multiple: number | null;
  gross_pnl: number;
  net_pnl: number;
  total_fees: number;
  avg_win: number | null;
  avg_loss: number | null;
  avg_holding_secs: number | null;
}

export interface EquityCurveStats {
  n_points: number;
  start_equity: number | null;
  current_equity: number | null;
  high_water_mark: number | null;
  max_drawdown_pct: number | null;
  total_return_pct: number | null;
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
  equity: number;
  high_water_mark: number;
  drawdown: number;
  exposure: number;
}

export interface Position {
  trade_id: number;
  instrument_symbol: string | null;
  opened_at: string;
  qty_lots: number;
  avg_entry: number;
  mark: number | null;
  unrealized: number | null;
  greeks: {
    iv: number | null;
    delta: number | null;
    gamma: number | null;
    theta: number | null;
    vega: number | null;
  } | null;
  stop_price: string | null;
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
  strike: number | null;
  expiry: string | null;
  opened_at: string;
  closed_at: string | null;
  avg_entry: number;
  avg_exit: number | null;
  gross_pnl: number | null;
  net_pnl: number | null;
  fees: number | null;
  r_multiple: number | null;
  mfe: number | null;
  mae: number | null;
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
  daily_pnl: number | null;
  weekly_pnl: number | null;
  consecutive_losses: number;
  breaker_status: string;
  breaker_reason: string | null;
  blocked_until: string | null;
  risk_multiplier: number | null;
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
  equity: number | null;
  equity_converted: number | null;
  high_water_mark: number | null;
  max_drawdown_pct: number | null;
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
