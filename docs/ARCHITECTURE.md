# Kairodex — Architecture for an AI-Assisted Options Buying Paper Trading Platform

**Status:** revised 2026-08-04 with all decisions applied (§21). P0 (foundations) implemented and verified against live vendor endpoints — see `docs/adr/` for decisions made during the build. P1 (the recorder) next.
**Companion doc:** [SPEC_REVIEW.md](./SPEC_REVIEW.md) — read that first; this document assumes its resolutions.

---

## 1. Principles

Five decisions that everything else follows from.

1. **One engine, two clocks.** Backtest, shadow, and live paper trading run *the same code*. The only difference is which `Clock` and which `MarketDataProvider` are injected. A golden test asserts that replaying a recorded session reproduces its live trades exactly. This is the highest-value decision in the document — backtest/live divergence is how platforms like this fail.
2. **The event log is the truth.** `trade_events` is append-only, hash-chained, and `UPDATE`/`DELETE` are revoked at the database role level. Every other trading table is a projection that can be rebuilt from it. This is what makes the spec's "complete and immutable record" real rather than aspirational.
3. **Point-in-time or it didn't happen.** Every feature row carries `as_of` — the instant all its inputs were observable. Backtests read `as_of <= clock.now()`. Instrument specs (lot size, tick size) are SCD-2 so a 2024 backtest uses NIFTY's lot of 25, not 75. This is the only defence against look-ahead bias, and it has to be structural.
4. **Modular monolith, multi-process.** One codebase, clean internal ports, deployed as ~8 processes. Segment isolation is enforced by the OS — one process per segment, separate DB row-space, separate capital — not by discipline. Microservices would add network failure modes to buy isolation we get for free from `--segment nse_index`.
5. **FastAPI is glue.** Routers, serialization, WebSocket fanout. Zero business logic. An import-linter rule in CI fails the build if `kairodex.api` imports anything from `kairodex.strategy`, `kairodex.risk`, or `kairodex.engine` beyond read-only repositories.

---

## 2. Component view

```
┌─────────────────────── DATA SOURCES ───────────────────────┐
│  Upstox (NSE)                    LondonStrategicEdge (US)  │
│  WS feed · chain · greeks        WS ticks · chain · flow   │
│  expired candles (Plus)          vault REST · replay       │
└───────┬───────────────────────────────────┬────────────────┘
        │  MarketDataProvider port (swappable adapters)
┌───────▼───────────────────────────────────▼────────────────┐
│  INGEST  · normalize · dedupe · quality-flag · tier T0/T1/T2│
└───────┬─────────────────────────────────────────────┬──────┘
        │                                             │
┌───────▼──────────────┐                    ┌─────────▼──────┐
│ TimescaleDB          │◄───────────────────┤ Redis          │
│ bars · quotes ·      │                    │ latest state   │
│ events · trades      │                    │ pub/sub · lock │
└───────┬──────────────┘                    └─────────┬──────┘
        │                                             │
┌───────▼─────────────────────────────────────────────▼──────┐
│  ENGINE  (×4, one per segment — identical code)            │
│                                                            │
│   Clock ──► Features ──► Detectors ──► Confluence Scorer   │
│                                             │              │
│              Risk Gate Chain ◄──────────────┘              │
│                    │                                       │
│              Contract Selector ──► Sizer ──► Execution     │
│                                               (Sim|Shadow) │
│              Position Monitor ──► Exit Rules               │
└───────┬────────────────────────────────────────────────────┘
        │ events + pub/sub
┌───────▼──────────┐  ┌──────────────┐  ┌───────────────────┐
│ FastAPI          │  │ jobs         │  │ research CLI      │
│ REST + WS        │  │ rollups      │  │ backtest · walk-  │
│ (thin)           │  │ exports      │  │ forward · Optuna  │
└───────┬──────────┘  │ token check  │  │ export bundle     │
        │             └──────────────┘  └─────────┬─────────┘
┌───────▼──────────┐                    ┌─────────▼─────────┐
│ Next.js          │                    │ Claude Code       │
│ 5 dashboards     │                    │ (external, human) │
└──────────────────┘                    └───────────────────┘
```

---

## 3. Process topology

Single box, `docker compose`, ~8 processes. Each is `kairodex <command>`.

| Process | Count | Responsibility | Restart policy |
|---|---|---|---|
| `ingest --market nse` | 1 | Upstox WS + REST poller, tiered subscription | always |
| `ingest --market us` | 1 | LSE WS + REST, quota-aware | always |
| `engine --segment {nse_stock,nse_index,us_stock,us_index}` | 4 | Features → signal → risk → execution → monitoring | always |
| `api` | 1 | FastAPI REST + WS fanout | always |
| `jobs` | 1 | APScheduler: EOD rollups, exports, annual token-expiry check, retention, FX snapshot | always |
| `research` | on demand | Backtests, walk-forward, Optuna, bundle export | CLI |

**Why one process per segment.** The spec says four times that segments must be independent. Four processes make that structural: `nse_stock` crashing cannot touch `us_index`; each has its own memory, its own connection pool, its own Redis lock. Cost is ~200MB RAM each. Same binary, four config files — no code duplication.

**Single-writer guarantee.** Each engine holds a Redis lock `engine:{segment}`; a second instance refuses to start. Prevents double-trading a segment after a bad restart.

---

## 4. Repository structure

```
OptionTradingSystem/
├── SPEC.md
├── docs/
│   ├── SPEC_REVIEW.md            # the critical review
│   ├── ARCHITECTURE.md           # this file
│   └── adr/                      # 0001-one-engine-two-clocks.md, ...
├── pyproject.toml                # uv, src layout, ruff + mypy strict
├── docker-compose.yml            # timescaledb-ha, redis, 8 app services
├── alembic/
├── src/kairodex/
│   ├── core/          # Money, Segment, Side, ids, Clock port, errors, tz
│   ├── config/        # pydantic-settings; config/segments/*.yaml
│   ├── data/
│   │   ├── ports.py       # MarketDataProvider, InstrumentMaster protocols
│   │   ├── upstox/        # auth + token lifecycle, ws feed, chain, candles, ratelimit
│   │   ├── lse/           # ws, vault REST, options flow, quota tracker
│   │   ├── normalize.py   # vendor payload → canonical Quote/Bar/ChainSnapshot
│   │   ├── quality.py     # staleness, gaps, crossed books, outliers
│   │   └── calendar.py    # sessions, holidays, expiry rules (data-driven)
│   ├── store/         # SQLAlchemy 2.0 models, repositories, hypertable DDL
│   ├── pricing/       # black76, bjerksund, iv_solve, greeks, forward, synthetic
│   ├── features/      # registry.py, compute/*.py, point-in-time store
│   ├── analysis/      # regime, volume_profile, gex, flow, structure, corr
│   ├── strategy/      # protocol, detectors/, scorer, registry, segments/*
│   ├── risk/          # gates/, sizing, breakers, exposure, correlation
│   ├── execution/     # ports.py, simulator.py, shadow.py, fills, costs/
│   ├── engine/        # orchestrator, clocks, live_loop, replay_loop, monitor
│   ├── backtest/      # runner, walkforward, purged_cv, metrics, deflated_sharpe
│   ├── analytics/     # performance, breakdowns, rollups, equity
│   ├── export/        # bundle builder, schemas/*.json, digest
│   ├── obs/           # structlog, metrics, alerts, health
│   ├── api/           # routers/, schemas/, ws.py, deps.py  (thin)
│   ├── jobs/          # scheduler + task modules
│   └── cli.py         # typer entrypoint for every process
├── tests/
│   ├── unit/  integration/  replay/          # golden session replay
│   └── fixtures/recorded_sessions/
├── frontend/          # Next.js app router, TS, Tailwind, shadcn/ui
└── data/              # gitignored: exports/, cache/, recordings/
```

---

## 5. Data model

PostgreSQL 16 + TimescaleDB (pinned `timescale/timescaledb-ha` image — do not use the Homebrew PG 18 on this box; the extension needs a matching build). All timestamps `timestamptz`, stored UTC. All money `numeric(18,4)` — never float.

### 5.1 Reference

```sql
CREATE TABLE instruments (
  instrument_id   bigserial PRIMARY KEY,
  segment         segment_enum,                  -- null for underlyings
  kind            instrument_kind,               -- UNDERLYING|INDEX|OPTION|FUTURE
  symbol          text NOT NULL,
  exchange        text NOT NULL,
  currency        char(3) NOT NULL,
  underlying_id   bigint REFERENCES instruments,
  strike          numeric(18,4),
  option_type     char(1),                       -- C|P — vendor spellings (Upstox CE/PE) normalized on ingest
  expiry          date,
  exercise_style  text,                          -- EUROPEAN|AMERICAN
  settlement      text,                          -- CASH|PHYSICAL
  provider_ids    jsonb NOT NULL,                -- {"upstox":"NSE_FO|12345","lse":"AAPL240119C00150000"}
  first_seen      timestamptz NOT NULL,
  last_seen       timestamptz NOT NULL,
  UNIQUE (exchange, symbol, expiry, strike, option_type)
);

-- SCD-2: backtests must use the lot size in effect at the time (NIFTY 25 -> 75)
CREATE TABLE instrument_specs (
  instrument_id bigint REFERENCES instruments,
  valid_from    date NOT NULL,
  valid_to      date NOT NULL DEFAULT 'infinity',
  lot_size      integer NOT NULL,
  tick_size     numeric(10,4) NOT NULL,
  PRIMARY KEY (instrument_id, valid_from)
);

CREATE TABLE trading_calendar (
  exchange text, session_date date, is_holiday boolean,
  open_utc timestamptz, close_utc timestamptz, tz text,
  PRIMARY KEY (exchange, session_date)
);

CREATE TABLE watchlist_membership (       -- point-in-time; kills survivorship bias
  segment segment_enum, instrument_id bigint,
  valid_from date, valid_to date DEFAULT 'infinity',
  tier smallint,                          -- 1 = T1, 2 = T2 eligible
  PRIMARY KEY (segment, instrument_id, valid_from)
);

CREATE TABLE corporate_actions (
  instrument_id bigint, ex_date date, action_type text,
  ratio numeric(18,8), adj_factor numeric(18,8), source text,
  PRIMARY KEY (instrument_id, ex_date, action_type)
);

CREATE TABLE fx_rates (as_of date, pair text, rate numeric(18,8), PRIMARY KEY (as_of, pair));
```

### 5.2 Market data (hypertables)

```sql
CREATE TABLE underlying_bars (
  instrument_id bigint, ts timestamptz, timeframe text,
  open numeric(18,4), high numeric(18,4), low numeric(18,4), close numeric(18,4),
  volume bigint, vwap numeric(18,4), source text, quality smallint,
  PRIMARY KEY (instrument_id, timeframe, ts)
);
SELECT create_hypertable('underlying_bars','ts', chunk_time_interval => interval '7 days');
-- 5m/15m/1h/1d derived via continuous aggregates from 1m — never stored twice

CREATE TABLE option_quotes (              -- the high-volume table
  instrument_id bigint, ts timestamptz,
  snapshot_id   uuid,                     -- groups one atomic chain read
  bid numeric(18,4), ask numeric(18,4), bid_sz integer, ask_sz integer,
  ltp numeric(18,4), volume bigint, oi bigint, oi_change bigint,
  underlying_px numeric(18,4),
  iv numeric(10,6), delta numeric(10,6), gamma numeric(14,10),
  theta numeric(12,6), vega numeric(12,6), rho numeric(12,6),
  greeks_model text, greeks_inputs jsonb,   -- forward, rate, t2e -> reproducible
  vendor_iv numeric(10,6),                  -- cross-check only, never source of truth
  tier smallint, source text, quality smallint,
  PRIMARY KEY (instrument_id, ts)
);
SELECT create_hypertable('option_quotes','ts', chunk_time_interval => interval '1 day');
ALTER TABLE option_quotes SET (timescaledb.compress,
  timescaledb.compress_segmentby = 'instrument_id', timescaledb.compress_orderby = 'ts DESC');
SELECT add_compression_policy('option_quotes', interval '7 days');

CREATE TABLE chain_snapshots (            -- completeness metadata per chain read
  snapshot_id uuid PRIMARY KEY, underlying_id bigint, ts timestamptz,
  contract_count integer, expected_count integer, latency_ms integer, complete boolean
);

CREATE TABLE market_depth (               -- T2 only, 30-day retention
  instrument_id bigint, ts timestamptz, side char(1), level smallint,
  price numeric(18,4), qty integer, orders integer,
  PRIMARY KEY (instrument_id, ts, side, level)
);

CREATE TABLE options_flow (               -- US only; LSE prints
  instrument_id bigint, ts timestamptz, price numeric(18,4), size integer,
  premium numeric(18,4), aggressor char(1), greeks_at_print jsonb,
  PRIMARY KEY (instrument_id, ts, price, size)
);
```

### 5.3 Features

```sql
CREATE TABLE feature_vectors (
  id bigserial PRIMARY KEY,
  segment segment_enum, instrument_id bigint,
  as_of timestamptz NOT NULL,            -- when ALL inputs were observable
  event_ts timestamptz NOT NULL,         -- what instant it describes
  registry_version text NOT NULL,
  values  jsonb NOT NULL,                -- {"atr_pct":0.83,"regime":"trend_up",...}
  quality jsonb NOT NULL,                -- per-feature: EXACT|PROXY|STALE|MISSING
  UNIQUE (segment, instrument_id, as_of, registry_version)
);
SELECT create_hypertable('feature_vectors','as_of', chunk_time_interval => interval '7 days');
```

`jsonb` deliberately: the feature set will churn weekly during research and a wide table would mean a migration per idea. Hot features get promoted to real columns once stable. *Ceiling: jsonb aggregation is ~3× slower than columns; promote when a dashboard query exceeds 200ms.*

### 5.4 Trading — the crown jewels

```sql
CREATE TABLE strategies (
  strategy_id bigserial PRIMARY KEY,
  segment segment_enum, name text, version integer,
  params jsonb, code_sha text,
  status strategy_status,              -- DRAFT|BACKTESTED|VALIDATED|SHADOW|PAPER_SMALL|PAPER_FULL|RETIRED
  UNIQUE (segment, name, version)
);

CREATE TABLE strategy_promotions (     -- every state change, human-attributed
  id bigserial PRIMARY KEY, strategy_id bigint, from_status strategy_status,
  to_status strategy_status, at timestamptz, approved_by text,
  validation_report jsonb, rationale text
);

CREATE TABLE signals (                 -- INCLUDING the ones we declined
  signal_id bigserial PRIMARY KEY,
  ts timestamptz, segment segment_enum, strategy_id bigint, underlying_id bigint,
  direction side_enum, confidence numeric(6,4),
  feature_vector_id bigint REFERENCES feature_vectors,
  evidence jsonb,                      -- [{detector, score, weight, contribution}]
  decision text,                       -- TAKEN | REJECTED
  reject_stage text, reject_reason text,
  forward_outcome jsonb                -- filled in later: did the move happen? (lead-time metric)
);

CREATE TABLE trades (
  trade_id bigserial PRIMARY KEY,
  run_id bigint REFERENCES backtest_runs,      -- NULL = live paper book
  segment segment_enum, strategy_id bigint, signal_id bigint,
  instrument_id bigint, underlying_id bigint,
  opened_at timestamptz, closed_at timestamptz,
  qty_lots integer, lot_size integer,          -- denormalized: the size in effect then
  avg_entry numeric(18,4), avg_exit numeric(18,4),
  premium_paid numeric(18,4), fees numeric(18,4),
  gross_pnl numeric(18,4), net_pnl numeric(18,4), r_multiple numeric(10,4),
  mfe numeric(18,4), mae numeric(18,4), holding_secs integer,
  exit_reason text,
  greeks_entry jsonb, greeks_exit jsonb,
  context_entry jsonb, context_exit jsonb,     -- regime, profile state, liquidity, flow
  risk_params jsonb, expected_r numeric(10,4),
  chart_ref jsonb                              -- {instrument,tf,t0,t1,overlays[]} — not a screenshot
);
CREATE VIEW paper_trades AS SELECT * FROM trades WHERE run_id IS NULL;

CREATE TABLE trade_events (            -- APPEND ONLY. source of truth.
  event_id bigserial PRIMARY KEY,
  trade_id bigint, seq integer, ts timestamptz,
  event_type text,                     -- SIGNAL_GENERATED|RISK_APPROVED|RISK_REJECTED|
                                       -- CONTRACT_SELECTED|SIZED|ORDER_PLACED|FILLED|
                                       -- PARTIAL_FILL|STOP_SET|STOP_MOVED|PARTIAL_EXIT|
                                       -- TARGET_HIT|TIME_EXIT|EVENT_EXIT|CLOSED|ARCHIVED
  payload jsonb NOT NULL,
  prev_hash bytea, hash bytea,         -- tamper-evident chain per trade
  UNIQUE (trade_id, seq)
);
REVOKE UPDATE, DELETE ON trade_events FROM kairodex_app;

CREATE TABLE orders (order_id bigserial PRIMARY KEY, trade_id bigint, ts timestamptz,
  instrument_id bigint, side side_enum, qty integer, order_type text,
  limit_price numeric(18,4), status text, sim_params jsonb);

CREATE TABLE fills (fill_id bigserial PRIMARY KEY, order_id bigint, ts timestamptz,
  qty integer, price numeric(18,4), spread_bps numeric(10,4),
  slippage_bps numeric(10,4), fill_model jsonb);

CREATE TABLE position_marks (          -- hypertable; powers MFE/MAE + equity curve
  trade_id bigint, ts timestamptz, mark numeric(18,4), unrealized numeric(18,4),
  underlying_px numeric(18,4), greeks jsonb, PRIMARY KEY (trade_id, ts));

CREATE TABLE equity_snapshots (        -- hypertable, per segment
  segment segment_enum, ts timestamptz, run_id bigint,
  cash numeric(18,4), unrealized numeric(18,4), realized numeric(18,4),
  equity numeric(18,4), exposure numeric(18,4), utilization numeric(8,4),
  high_water_mark numeric(18,4), drawdown numeric(8,4),
  PRIMARY KEY (segment, ts, run_id));

CREATE TABLE risk_state (
  segment segment_enum PRIMARY KEY, as_of timestamptz,
  daily_pnl numeric(18,4), weekly_pnl numeric(18,4), consecutive_losses integer,
  breaker_status text, breaker_reason text, blocked_until timestamptz,
  risk_multiplier numeric(6,4));
```

### 5.5 Research loop & ops

```sql
CREATE TABLE research_notes (          -- imported from external Claude Code review
  note_id bigserial PRIMARY KEY, created_at timestamptz, segment segment_enum,
  bundle_id text, source text DEFAULT 'claude-code',
  findings jsonb, actions jsonb, applied_strategy_ids bigint[], status text);

CREATE TABLE backtest_runs (run_id bigserial PRIMARY KEY, created_at timestamptz,
  segment segment_enum, strategy_id bigint, from_ts timestamptz, to_ts timestamptz,
  config jsonb, metrics jsonb, trial_count integer, deflated_sharpe numeric(10,6));

CREATE TABLE feed_health (market text, ts timestamptz, connected boolean,
  msgs_per_sec numeric, gap_secs numeric, clock_skew_ms integer,
  quota_used numeric, PRIMARY KEY (market, ts));

CREATE TABLE audit_log (id bigserial PRIMARY KEY, ts timestamptz, actor text,
  action text, target text, before jsonb, after jsonb);
```

---

## 6. Capacity plan

The spec never asks, but naive full-chain recording is what kills this project on a single box.

**Naive:** 54 NSE underlyings × 240 contracts × 4,500 snapshots/day (5s) ≈ **58M rows/day**. Not viable.

**Tiered — three fidelity levels:**

| Tier | Scope | Cadence | What's stored |
|---|---|---|---|
| **T0 Universe** | all F&O underlyings + US watchlist | daily | instrument master, EOD bars |
| **T1 Watchlist** | ~20 underlyings/segment | 60s | 1m underlying bars, full chain (2 expiries × ~40 strikes × 2) |
| **T2 Focus** | underlyings with a live candidate or open position | 1–5s | chain window ATM±10, depth, ticks, flow |

Promotion T1→T2 is driven by the scanner and released when the candidate dies or the position closes.

**Resulting volume:** T1 ≈ 1.2M rows/day/market, T2 ≈ 0.9M → **~2M rows/day/market, ~4M/day total.** Raw ≈ 1–1.5 GB/day; with Timescale columnar compression (10–20× on this shape) ≈ **80–120 GB/year.** Fits a laptop; fits a cheap VPS.

**Retention:** T2 raw 30 days → downsampled to 1m. T1 1m kept indefinitely, compressed after 7 days. `market_depth` 30 days, no exception. Continuous aggregates for 5m/15m/1h/1d.

This tiering also happens to be forced by the vendors: Upstox caps instruments per websocket connection, and LSE shares one allowance across streaming and download.

---

## 7. Ingestion

`MarketDataProvider` port — every vendor behind it, so swapping Upstox or LSE is an adapter, not a rewrite:

```python
class MarketDataProvider(Protocol):
    async def instruments(self) -> AsyncIterator[InstrumentRecord]: ...
    async def subscribe(self, keys: list[str], mode: FeedMode) -> AsyncIterator[Tick]: ...
    async def chain(self, underlying: str, expiry: date) -> ChainSnapshot: ...
    async def bars(self, key: str, tf: Timeframe, start, end) -> list[Bar]: ...   # backtest source
    async def quota(self) -> QuotaStatus: ...
```

**Pipeline:** vendor payload → `normalize` (canonical types, UTC, instrument_id resolution) → `quality` (staleness, crossed book, zero-volume, outlier, sequence gap → `quality` bitmask) → dedupe `ON CONFLICT DO NOTHING` on the natural key → batch write (Timescale `COPY`, 1–5s batches) → Redis latest-state → Redis pub/sub.

**Reliability, mapped to the spec's list:**

| Failure | Handling |
|---|---|
| API outage | Exponential backoff + jitter, circuit breaker per endpoint, `feed_health` row, alert after N seconds |
| Rate limits | Token-bucket sized from documented limits; LSE additionally polls `/vault/usage` and sheds T2 first |
| Missing data / delayed feeds | Staleness watermark per instrument; engine refuses to trade on quotes older than `max_quote_age_ms` |
| Duplicate data | Natural-key upsert, idempotent by construction |
| Corrupted records | Quality bitmask; poisoned rows land in `ingest_quarantine`, never silently dropped |
| Partial chain reads | `chain_snapshots.complete = false`; incomplete chains are unusable for signals but still stored |
| Clock issues | `clock_skew_ms` from vendor server time; entries halt above 2s |
| Restart | Resume from `max(ts)` per instrument, backfill the gap via REST, rebuild engine state from `trade_events` |
| **Upstox token expiry** | Analytics Token is long-lived (1yr, ADR 0006), not daily — a monthly job checks `AnalyticsToken.is_expiring_soon()` and alerts well before the ~1-year mark |

---

## 8. Pricing

One module, both markets, so cross-segment comparison is apples-to-apples.

- **European** (all NSE options; SPX/NDX/RUT): **Black-76 off the synthetic forward** derived from the futures price or put-call parity. Deliberate: it removes the need to guess a risk-free rate and dividend yield on NSE, which is the main source of Greek error there.
- **American** (US equity/ETF options): Bjerksund-Stensland 2002 closed form; Greeks by central finite difference. *Ceiling: BS2002 is an approximation — upgrade to CRR binomial if deep-ITM accuracy matters.*
- **IV solve:** Brent on price, bracketed, with a Jäckel-style initial guess. Fail → `quality = NO_IV`, never a silently wrong number.
- **Reproducibility:** every stored Greek carries `greeks_model` + `greeks_inputs` (forward, rate, time-to-expiry, underlying, timestamp). Any Greek in the database can be recomputed years later.
- **Synthetic-option overlay:** prices an option along a backtested underlying path from an IV surface assumption, for the Track A second pass (§13). Flagged `ESTIMATE`; **never** counted toward a promotion gate.
- Validated against `py_vollib` in unit tests.

*Note: historical option-chain reconstruction from expired contracts is out of scope following the SPEC_REVIEW A3 decision. Pricing serves the live engine (contract selection, monitoring, risk) and the synthetic overlay — not backtesting.*

---

## 9. Feature registry

The spec lists ~40 market-analysis concepts. That's a backlog, not a milestone. Each becomes a registered feature declaring what it needs and how good it is:

```python
@register(
    name="oi_delta_imbalance",
    inputs=[Input.CHAIN_OI, Input.UNDERLYING_1M],
    tier=Tier.T1,
    fidelity=Fidelity.PROXY,       # EXACT | PROXY | ESTIMATE
    backtestable={"nse": True, "us": True},
    cost_ms=3,
)
def oi_delta_imbalance(ctx: FeatureContext) -> float: ...
```

Two attributes carry the weight:

- **`fidelity`** — `absorption`, `bid_ask_imbalance`, and `smart_money_footprint` on NSE are reconstructed from L1/L2 snapshots via the tick rule, not from true aggressor-tagged prints. They are proxies and are labelled as such everywhere, including in the export bundle. US has real prints via LSE `options_flow`, so the same feature is `EXACT` there. Without this attribute, someone eventually reads a proxy as ground truth.
- **`backtestable`** — depth-derived features are false for NSE. The promotion pipeline reads this and routes any strategy depending on them to shadow-mode validation only.

**Launch set (~15), in dependency order:** ATR/realized vol · volatility regime (expansion/contraction) · trend state + strength · VWAP position & bands · opening range · volume profile (POC/VAH/VAL) · price acceptance/rejection · relative strength vs index · index correlation · IV rank/percentile · IV skew · term structure · OI change & PCR positioning · net gamma exposure estimate · liquidity score (spread, depth, OI, volume).

Deferred to the backlog: auction theory, market profile TPO, dealer positioning, liquidity sweeps, absorption/exhaustion, inter-market relationships, sector rotation. Each is one registry entry when its data dependency is proven.

Computed once per evaluation tick, written to `feature_vectors` with `as_of`, read identically by live and replay. Train/serve skew is impossible by construction.

---

## 10. Strategy framework

```python
class Strategy(Protocol):
    id: StrategyId
    required_features: frozenset[str]
    params: BaseModel                              # pydantic; hashed into version
    def evaluate(self, ctx: MarketContext) -> list[Evidence]: ...
    def manage(self, pos: Position, ctx: MarketContext) -> ExitDecision | None: ...
```

**Confluence, not indicators.** §Market Analysis demands multiple independent observations and forbids indicator-only logic. Implemented literally: a strategy is a set of `Detector`s each emitting `Evidence(name, score[-1..1], weight, rationale)`. A `ConfluenceScorer` combines them, requiring **≥ N independent detector families** (structure / flow / volatility / relative-strength) to agree before a signal is emitted. Single-family agreement can never fire, whatever the score.

Every `Evidence` item is persisted on the signal with its contribution. Explainability is free: the dashboard and the export bundle both show exactly why a trade happened — or why one didn't.

**Promotion state machine.** Every transition human-approved and written to `strategy_promotions`:

```
DRAFT → BACKTESTED → VALIDATED → SHADOW → PAPER_SMALL → PAPER_FULL
                                    └──────────┴─────────────┴──→ RETIRED
```

**Promotion gates** (config, not hardcoded — my proposed definition of the spec's undefined "statistically meaningful"). Split by track, because the two tracks now measure genuinely different things:

**Track A — directional, on underlying OHLCV.** `BACKTESTED → VALIDATED`:

| Gate | Threshold |
|---|---|
| Sample size | ≥ 200 signals (cheap on underlying history — no reason to accept less) |
| Directional hit rate | > break-even for the strategy's own MFE/MAE profile |
| Expectancy | > 0 in ATR units, bootstrap 95% CI excludes zero |
| MFE/MAE ratio | ≥ 1.5 |
| Signal lead time | ≥ 60% of the subsequent move occurs *after* the signal |
| Consistency | positive expectancy in ≥ 3 of 4 quarters **and** ≥ 2 volatility regimes |
| Multiple testing | Deflated Sharpe > 0 at the honest trial count |
| Walk-forward efficiency | ≥ 0.5 (OOS / IS) |
| Synthetic overlay | not a gate — but a negative overlay requires written justification on the promotion record |

**Track B — option economics, live shadow on real chains.** `SHADOW → PAPER_SMALL`:

| Gate | Threshold |
|---|---|
| Sample size | ≥ 100 shadow trades, **or** bootstrap 95% CI on expectancy excludes zero |
| Profit factor | ≥ 1.3 **after modelled costs** |
| Max drawdown | ≤ 15% of segment capital |
| Slippage realism | realised simulated slippage within tolerance of the model's assumption |
| Directional agreement | shadow hit rate within 1 SE of the Track A hit rate |

That last gate is the one that catches self-deception: if live results diverge from the backtest, the backtest was wrong and the strategy does not advance.

**NSE sample-size note.** Because NSE runs on ₹50,000 with 1–2 concurrent positions (SPEC_REVIEW A2), the NSE segments will take considerably longer to reach 100 shadow trades. Their Track B gate therefore leans on the **CI criterion rather than the raw count** — a strategy with a tight, clearly-positive confidence interval over 40 trades may promote; one with 120 noisy trades may not. Sample size is a proxy for confidence, and where the proxy is expensive we measure confidence directly.

---

## 11. Risk engine

A chain of independent gates. Every gate returns allow/deny + reason; **every denial is written to `signals` as a rejected signal.** The rejections are training data.

**Order:** kill switch → breaker state → session/time window → event blackout (earnings, macro) → daily loss → weekly loss → drawdown throttle → max concurrent → exposure % → correlation cluster → liquidity (spread, OI, volume, depth) → capital available → **1-lot risk ceiling**.

**Sizing:**

```
risk_budget = equity × base_risk_pct × risk_multiplier(equity, hwm, recent_perf)
lots        = floor(risk_budget / (stop_distance × lot_size))
if lots < 1: reject NO_TRADE_MIN_SIZE
if risk(1 lot) > hard_ceiling_pct × equity: reject SIZE_EXCEEDS_CEILING
```

`risk_multiplier` is the spec's "scale up on profit, reduce on loss", made explicit: a config-driven curve over equity/HWM and recent performance. Always computed against **current equity**, never initial capital.

**Per-segment risk config.** Capital was kept at the specified levels (SPEC_REVIEW A2), so NSE necessarily runs at elevated risk. That is configured openly rather than hidden:

| Segment | Capital | `base_risk_pct` | `hard_ceiling_pct` | `max_concurrent` |
|---|---|---|---|---|
| NSE Stock | ₹50,000 | 8% | 35% | 5 |
| NSE Index | ₹50,000 | 7% | 25% | 2 |
| US Stock | $50,000 | 1.5% | 3% | 6 |
| US Index | $50,000 | 1.5% | 3% | 6 |

This column was originally "realistic concurrency" — a prediction of what
the capital allows (NSE Stock 0–1) rather than the configured cap. It now
states the actual `max_concurrent` in `config/segments/*.yaml`, since that
is the value the gate chain enforces and the value the test suite pins.
NSE Stock was raised 1 → 5 on 2026-08-06 (user's call). Note that this
raises a ceiling the capital math still sits below: `exposure_cap_pct`
0.40 caps total exposure at ₹20,000 while `max_premium_pct` 0.35 allows
₹17,500 in any one position, so the exposure gate — not `max_concurrent` —
remains the binding constraint on this book. The higher cap lets several
genuinely small positions coexist; it does not authorise five full-size
ones.

Every trade record stores the risk parameters in force, and both the dashboard and the export bundle display the segment's risk profile alongside its returns — so NSE performance is never read as if it were achieved at US risk levels. Cross-segment comparison on the Master Dashboard is therefore shown **risk-adjusted by default**; raw P&L comparison across segments at 5× different risk-per-trade would be actively misleading.

**Affordability constraint** (also from A2). Promoted from the spec's "capital efficiency" wish-list item to a hard pre-trade gate, because on a ₹50,000 book it binds constantly:

```
premium × lot_size ≤ max_premium_pct × equity      # NSE: 0.35 / US: 0.05
```

Applied at two points: the NSE Stock watchlist is pre-filtered to underlyings whose median ATM premium/lot can clear it, and the contract selector treats it as a constraint rather than a preference. In practice it biases NSE toward slightly OTM strikes — a real and defensible response to a small book. Every exclusion is logged, so the export bundle shows exactly which opportunities capital ruled out.

**Controls, mapped to the spec:** max risk/trade · daily & weekly loss limits · max DD · exposure caps · max concurrent · correlation-aware clustering (positions in correlated underlyings share one budget) · volatility-adjusted sizing · auto stop-loss · trailing stop · partial exits at R-multiples · profit targets · time-based exit (theta guard) · event-based exit · per-segment circuit breaker · global kill switch · **no averaging down, no pyramiding, no re-entry within N minutes after a loss** (anti-revenge, enforced in the gate chain rather than left to strategy authors).

**Paper-only enforcement, structurally:** the `ExecutionPort` has exactly two implementations, `SimulatedBroker` and `ShadowLogger`. No broker order-placement credential exists anywhere in the codebase or config schema. A startup assertion fails hard if `PAPER_ONLY` is not true. Live trading is not a flag — it's absent code.

---

## 12. Execution simulator

The spec wants realism, so the model is explicit and configurable rather than optimistic:

- **Latency:** signal → order delay, configurable (default 250ms + jitter). Quotes older than `max_quote_age_ms` at fill time are rejected.
- **Fill:** marketable orders only in v1. Fill price = `mid ± k × half_spread`, `k` per segment (default 0.6 — worse than mid, better than full spread). *Skipped passive/queue-position modelling; add when a strategy actually rests orders.*
- **Partial fills:** fill qty capped at `α × top-of-book size` (default 0.25). Remainder re-attempts next tick, expires after N attempts.
- **Rejection:** spread > `max_spread_bps`, size > `max_pct_of_oi`, or chain snapshot incomplete → no fill, logged.
- **Costs:** pluggable `CostModel` per segment. NSE: brokerage, STT on sell premium, exchange transaction charge, SEBI turnover fee, stamp duty, GST on the fee stack. US: per-contract commission, OCC/ORF, SEC/TAF on sells. **Rates live in config, seeded from current published values — verify against a real contract note before trusting backtest P&L.**
- **Marking:** open positions marked every tick into `position_marks`, which is what makes MFE/MAE exact rather than estimated from OHLC.

The same simulator runs in backtest and live paper. `ShadowLogger` wraps it: computes the identical fill, writes the identical events, allocates zero capital.

---

## 13. Backtest & replay

**Scope, per the decision in SPEC_REVIEW A3: backtesting runs on underlying OHLCV, not on option contracts.** The question Track A answers is *"does this algorithm read direction and timing correctly?"* Option economics are Track B's job.

`ReplayClock` drives the identical orchestrator — same detectors, same confluence scorer, same feature registry, same `as_of` discipline. Only the clock and the data source change.

**Data sources:** Upstox candles v3 (NSE underlyings), LSE vault (US equities to 2003, 14 resolutions, native replay). Both free. Recorded T1/T2 sessions are used for the golden replay test and for Track B analysis, not for Track A.

**Track A output — directional metrics, in market units rather than currency:**

| Metric | Why |
|---|---|
| Hit rate | did direction resolve correctly |
| MFE/MAE ratio | was the move asymmetric in our favour |
| Expectancy in ATR units | edge normalized across instruments and vol regimes |
| **Signal lead time** | §Early Trade Detection made measurable (SPEC_REVIEW C10) — how much of the move came *after* the signal |
| MFE capture ratio | did we signal early enough to catch the strong part |
| Stability across regimes / quarters | robustness, not curve fit |

**Synthetic-option overlay — second pass, estimate only.** Prices a Black-76 option along the backtested underlying path using an IV assumption drawn from the current surface, and reports estimated option P&L beside the directional metrics. It answers the question that decides an options *buying* system: *is this move big enough and fast enough to beat theta and IV crush?* A strategy with a strong directional edge and a negative synthetic overlay is signalling correct direction on moves too small to monetise — worth knowing before it consumes weeks of shadow time. Labelled `ESTIMATE` everywhere and **never counted toward a promotion gate.**

**Rigour, unchanged:**

- **Walk-forward** with purged, embargoed splits (embargo ≥ max holding period) so a trade straddling a boundary can't leak.
- **Deflated Sharpe** using the honest trial count from `optimization_runs`.
- **A held-out final period** no research run may touch, enforced by a config guard.
- **Golden replay test:** `tests/replay/` holds a recorded live session; the test asserts replay reproduces its trades exactly. This is the guard on Principle 1 and the most valuable test in the suite — and the reason Backtrader is not here.

---

## 14. Export bundle — the Claude Code loop

The spec makes this a first-class deliverable ("well-structured, standardized, and queryable … no critical context ever omitted"), so it gets a versioned schema rather than an ad-hoc dump.

`kairodex export --segment nse_index --from 2026-07-01 --to 2026-07-31 --out data/exports/`

```
bundle_nse_index_2026-07/
├── manifest.json          # schema_version, filters, row counts, sha256 per file,
│                          # code_sha, strategy versions active in the window
├── trades.jsonl           # one complete trade per line, fully denormalized
├── trade_events.jsonl     # full lifecycle event log
├── signals_rejected.jsonl # every declined opportunity + stage + reason
├── equity.csv             # daily equity, drawdown, exposure
├── performance.json       # all metrics, plus every breakdown
├── feature_dictionary.json# what each feature means, how computed, EXACT/PROXY, backtestable
├── data_quality.json      # feed gaps, incomplete chains, reconstructed Greeks in the window
├── digest.md              # token-budgeted human/LLM summary — paste this into Claude Code
└── README.md              # generated: how to read the bundle, known caveats
```

`feature_dictionary.json` and `data_quality.json` are the anti-misinterpretation machinery the spec asks for. Without them, an external reviewer cannot know that `absorption` on NSE is a proxy or that a week's Greeks were reconstructed — and will draw confident wrong conclusions.

Return path: `kairodex research import-notes findings.json` → `research_notes`, linked to the strategy versions it causes. The loop closes and stays auditable.

---

## 15. API surface

FastAPI, thin, read-mostly. Writes are limited to human control actions, all audited.

```
GET  /api/health                       /api/health/feeds
GET  /api/segments                     /api/segments/{seg}/overview
GET  /api/segments/{seg}/positions     /api/segments/{seg}/opportunities
GET  /api/segments/{seg}/trades?from&to&strategy&outcome&page
GET  /api/segments/{seg}/trades/{id}          # full event timeline + chart_ref
GET  /api/segments/{seg}/signals?decision=rejected
GET  /api/segments/{seg}/performance?window
GET  /api/segments/{seg}/equity-curve
GET  /api/segments/{seg}/analytics/breakdown?by=regime|weekday|session|expiry|moneyness|vol_regime
GET  /api/segments/{seg}/risk
GET  /api/master/overview?ccy=INR|USD|native
GET  /api/instruments/{id}/chain?at=<ts>
GET  /api/strategies                   /api/strategies/{id}/report
GET  /api/research/notes
POST /api/strategies/{id}/promote      # human-gated, audited
POST /api/segments/{seg}/breaker       # trip / re-arm, audited
POST /api/kill                         # global halt, audited
POST /api/exports                      GET /api/exports/{id}
POST /api/backtests                    GET /api/backtests/{id}
WS   /ws/stream?segments=...           # typed messages, one socket for the whole UI
```

WebSocket messages are a discriminated union (`tick`, `signal`, `position_update`, `trade_closed`, `risk_update`, `feed_health`), Redis pub/sub → fanout. One socket, not one per panel.

---

## 16. Frontend

Next.js (app router) + TypeScript + Tailwind + shadcn/ui + TradingView Lightweight Charts, per spec.

**Five dashboards, two routes.** `/` (master) and `/segment/[segment]`. The spec asks for four dedicated segment dashboards "with the same level of detail" — same detail means the same component, parameterized, with segment-specific panels supplied by a small config map (NSE gets expiry-day and OI-buildup panels; US gets options-flow and GEX panels). Four copies of one dashboard would be four times the maintenance and would drift within a month.

**Master:** combined equity (native + converted), segment comparison, allocation, utilization, recent trades, risk summary, current opportunities, system health, feed status, strategy health, **Research Insights** (the `research_notes` panel replacing "AI insights" per SPEC_REVIEW A1).

**Segment:** market state, live opportunities with confluence breakdown, open positions with live Greeks, trade history with drill-down to the full event timeline, performance, risk, charts, strategy stats, capital utilization.

Server components for static reads, one WS subscription for live state, TanStack Query for the rest.

---

## 17. Observability

- **Logging:** `structlog` JSON, every line carrying `segment`, `strategy_id`, `trade_id`, `correlation_id`. Every event in the spec's list has a named event type.
- **Metrics:** Prometheus endpoint — ingest lag, quote staleness, decisions/min, gate rejection counts by reason, fill slippage distribution, engine loop time, DB write latency, vendor quota consumed.
- **Alerts** (missing from the spec, §C3): breaker tripped, feed down > N sec, token expiring, ingestion gap, engine crash, disk > 80%. Sink is pluggable; default desktop notification + webhook.
- **Health:** `/api/health/feeds` reports per-market connection state, last message age, clock skew, quota headroom.

---

## 18. Testing

- **Unit:** pricing vs `py_vollib` and known values; every risk gate; cost models against real contract notes; fill model edge cases.
- **Property:** sizing never exceeds the risk ceiling; `sum(fills) == trade.net_pnl + fees`; equity is monotonic with realized P&L.
- **Integration:** ingest → store → feature → signal → risk → fill, against recorded vendor payloads (no live calls in CI).
- **Golden replay:** recorded session in, exact expected trades out.
- **Live/replay equivalence:** replay a recorded live session; assert identical decisions.
- **Architecture:** import-linter enforces `api ↛ strategy|risk|engine` and `engine ↛ optuna`.

No mocking of the database — testcontainers with real TimescaleDB. Mocked SQL hides the bugs that matter here.

---

## 19. Roadmap

Ordered by risk retirement and by data urgency. Effort is relative sizing, not a commitment.

| Phase | Deliverable | Exit criteria |
|---|---|---|
| **P0 — Foundations & vendor spike** (~1 wk) | Repo, config, compose (Timescale + Redis), both adapters authenticating, instrument master ingesting, ADRs written | A script pulls a live NSE chain and a live US chain into Timescale. Real rate limits, quotas, and field coverage documented — not assumed. |
| **P1 — Recorder** ⚠️ **highest priority** (~2 wks) | Tiered T0/T1/T2 ingestion, normalization, quality flags, feed health, compression + retention, WS streaming for both vendors, annual token-expiry alerting, restart recovery, minimal status page | 5 consecutive sessions recorded, < 0.5% gap rate on T1, clean restart mid-session |
| **P2 — Pricing & features** (~2 wks) | Black-76 + Bjerksund, IV solve, forward derivation, feature registry, ~15 launch features, point-in-time store | Our Greeks match vendor Greeks within tolerance; a feature computed live is **bit-identical** to the same feature computed in replay |
| **P3 — Engine & paper execution** (~3 wks) | Clock abstraction, orchestrator, detectors + confluence scorer, contract selector, risk gate chain, sizer, simulator + cost models, position monitor, event log, one reference strategy per segment | Full lifecycle runs in **shadow mode** for 5 sessions; PnL identity and risk-ceiling property tests pass |
| **P4 — Backtest & validation** (~1.5 wks) | ReplayClock over underlying OHLCV, directional metrics + signal lead time, synthetic-option overlay, walk-forward, purged CV, deflated Sharpe, promotion state machine + both gate sets | **Replaying a recorded live session reproduces its trades exactly** |
| **P5 — Analytics & export** (~2 wks) | All metrics + breakdowns, rollups, export bundle with JSON Schema, digest, `research_notes` import | One month of shadow data exported and reviewed end-to-end in Claude Code, findings imported back |
| **P6 — API & dashboards** (~3 wks) | FastAPI read layer, WS stream, Next.js master + parameterized segment dashboard, charts | All five dashboards live; p95 < 200ms on overview endpoints |
| **P7 — Strategy build-out & hardening** (ongoing) | Real strategies per segment, backlog features, chaos testing, alert tuning | First strategy reaches `PAPER_FULL` through the real gates |

**Why the recorder comes first and dashboards come sixth.** Every session that passes before P1 ships is market data that cannot be bought back — Upstox sells no historical Greeks, IV, or depth at any price, and the depth-derived features in §Market Analysis can *only* ever be validated on data we recorded ourselves. Dashboards built over an empty or wrong database are theatre. P1 ships a minimal status page so you are not building blind for three months, but the real UI waits until there is something true to show.

---

## 20. Trade-off register

| Decision | Chose | Gave up | Revisit when |
|---|---|---|---|
| Own engine vs Backtrader | One engine, two clocks | A maintained library's ecosystem | Never — the duplication cost is unbounded |
| Backtest on underlying OHLCV | Free, deep history; huge scope cut | Real option-chain backtesting | If a strategy repeatedly passes Track A and dies in Track B, the gap is option economics — revisit then |
| NSE at ₹50,000 | Spec fidelity | Multi-position risk management on NSE | If NSE Stock produces < 5 trades/month for two months |
| Modular monolith vs microservices | Monolith, 8 processes | Independent scaling/deploy | Multi-node, or a segment needs different hardware |
| Process per segment | 4 engine processes | ~800MB RAM | Never — isolation is a hard spec requirement |
| `jsonb` features | Schema agility during research | ~3× slower aggregation | A dashboard query exceeds 200ms → promote to columns |
| Marketable orders only | Simplicity | Passive fill modelling | A strategy needs to rest orders |
| No Celery | APScheduler + jobs table | Distributed task queue | Backtests need multi-node |
| No object storage | Chart refs + local exports | Blob durability | Off-site backup or multi-node |
| Shared `trades` table with `run_id` | One analytics code path | Risk of forgetting the filter | Mitigated by `paper_trades` view — always query the view |
| Bjerksund-Stensland | Closed-form speed | Deep-ITM accuracy | Pricing error materially affects contract selection |
| Compute Greeks in-house | Cross-market comparability | Vendor convenience | Never |

---

## 21. Decisions applied

All blocking items resolved 2026-08-04 — full record in SPEC_REVIEW §G. What changed in this document as a result:

| Decision | Architectural effect |
|---|---|
| Keep NSE capital at ₹50,000 | §11 per-segment risk table (NSE at 7–8%), affordability gate, risk-adjusted default on Master Dashboard, CI-weighted NSE promotion gate |
| Backtest on underlying OHLCV; no Upstox Plus | §13 rewritten — directional metrics + synthetic overlay; §10 gates split by track; §8 loses historical reconstruction; §7 loses the expired-contracts port method; P4 shortened |
| Drop Backtrader | §1 Principle 1 intact; replay loop now ~200–300 LOC |
| Drop Celery, MinIO, TA-Lib, PyFolio | §3 topology (no broker/worker), §4 tree, §20 register |
| Recorder first | §19 unchanged — P1 remains the priority, minimal status page included |
| US Index = SPX/NDX/RUT · INR reporting · no model inference | §5 `exercise_style`, §15 `?ccy=`, §16 Research Insights panel |

**Nothing is outstanding.** Ready to start P0 on your go-ahead.
