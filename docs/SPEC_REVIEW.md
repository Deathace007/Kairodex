# SPEC.md — Critical Review

**Project name:** Kairodex.
**Status:** reviewed; all blocking decisions resolved 2026-08-04 (§G). P0 (foundations) implemented — see `docs/adr/`.
**Reviewed:** 2026-08-04 against SPEC.md (988 lines).
**Companion doc:** [ARCHITECTURE.md](./ARCHITECTURE.md)

> Decisions are recorded inline in blockquotes and summarised in **§G**. Concerns raised before a decision are left standing as the record of why the choice was made — they are not re-argued.

---

## Verdict

The spec is unusually clear about *intent* and unusually vague about *constraints*. The vision is coherent and buildable. Four things in it are, as written, mutually impossible or arithmetically false, and they need your decision before a line of code is worth writing. Everything else is ordinary ambiguity that I can resolve with stated assumptions.

The single most valuable thing in the spec is §"AI-Assisted Research Loop" — the decision to keep learning *out* of the system and do it externally. That constraint is what makes this project finishable. Most of my recommendations below exist to protect it.

The single biggest risk is not in the spec at all: **historical options data availability differs enormously between your two vendors**, and the spec's central quality gate ("only statistically validated strategies may trade") silently assumes you have it for both. You don't.

---

## A. Blocking contradictions

These four cannot be resolved by me picking a sensible default.

### A1. The AI paradox

The spec says both things:

| Says AI is in the system | Says AI is not in the system |
|---|---|
| Title: "AI-Driven" platform | §AI Loop: "should **not** include any autonomous AI feedback loop or self-learning mechanism inside the system" |
| L3: "continuously improve its own trading performance through … AI-assisted optimization" | §AI Loop: "all learning … handled externally by manually providing trade history … to Claude Code" |
| L14: "AI-driven feedback" | §AI Loop: "improvements … applied only after external review and validation" |
| L39: each segment has "Separate AI feedback" | |
| L738: Master Dashboard shows "AI insights" | |
| L806: log "AI analysis" events | |
| L988: "self-improving quantitative research ecosystem" | |

**Resolution I propose:** the §AI Loop section wins — it is the most recent, most specific, and most operationally sound statement. Everything else is aspirational framing.

Concretely: **no model inference runs anywhere in the trading path.** The system is fully deterministic and explainable. The "AI" in the product is the human-gated external loop:

```
system observes → stores → exports bundle → you review in Claude Code
   → you write findings back in → strategy version bump → human promotes
```

Dashboard "AI insights" becomes a **Research Insights** panel rendering `research_notes` — findings you imported from an external Claude Code session, linked to the strategy versions they caused. "AI analysis" log events become `RESEARCH_NOTE_IMPORTED` / `STRATEGY_PROMOTED` audit events. This makes the manual loop *auditable*, which is strictly better than a black box and costs almost nothing to build.

**This also kills a whole category of scope.** No feature store for ML training, no model registry, no inference service, no GPU. If you later want ML, the exported dataset is exactly the right input for training it — outside this system.

### A2. ₹50,000 does not fund two of the four segments

This is arithmetic, not opinion.

SEBI's Nov 2024 rules set index derivative contract value at **₹15–20 lakh**; NIFTY's lot went 25 → 75. Stock derivative lots target ₹5–10 lakh notional.

| Segment | Typical premium/lot | % of ₹50,000 | Risk/trade at a 40% stop |
|---|---|---|---|
| NSE Index (NIFTY ATM weekly, 75 × ~₹120) | ~₹9,000 | 18% | **~7.2% of equity** |
| NSE Index (BANKNIFTY ATM, 35 × ~₹300) | ~₹10,500 | 21% | **~8.4% of equity** |
| NSE Stock (ATM monthly, ~3% of ₹7L notional) | ₹15,000–40,000 | **30–80%** | **12–32% of equity** |
| US Index (SPX/SPY, 100 × $3–5) | $300–500 | 0.6–1% | ~0.3% of equity |
| US Stock (100 × $5–10) | $500–1,000 | 1–2% | ~0.6% of equity |

Professional risk-per-trade is 1–2% of equity. The spec demands *simultaneously*: "Dynamic position sizing", "Maximum risk per trade", "Maximum simultaneous trades", "Correlation-aware exposure", and "Risk must always adapt according to current account equity."

**At ₹50,000, NSE Index can hold at most 2–3 positions at 4× the professional risk limit, and NSE Stock Options cannot hold one position without risking a third of the account.** You cannot build correlation-aware multi-position exposure management on a book that fits one contract. The US segments are fine — $50,000 is generous there.

Note the second-order damage: an unfundable segment doesn't just trade badly, it **produces a useless dataset**. Sample sizes stay tiny, and every trade is dominated by lot-size granularity rather than by the strategy. The whole point of the build is the dataset.

> ### ✅ DECIDED: keep ₹50,000, as specified.
>
> Accepted. NSE runs single-position at 6–12% risk per trade. The concern above stands on the record; it is not re-litigated below. What follows is how the design makes ₹50,000 work as well as it can.

**Accommodations built in because of this decision:**

1. **Affordability-filtered watchlist.** NSE Stock watchlist is restricted at selection time to underlyings whose median ATM premium × lot size fits the segment's per-trade budget. Low-notional, low-IV F&O names have per-lot premiums of ₹8,000–15,000 and are tradeable at 1 lot; ₹40,000-per-lot names are excluded automatically rather than generating endless rejections.
2. **Affordability as a contract-selection criterion.** §Option Contract Selection already lists "Capital efficiency". It is promoted to a hard constraint: `premium × lot_size ≤ max_premium_pct × equity`. In practice this biases NSE toward slightly OTM strikes, which is a real and defensible response to a small book — not a fudge.
3. **Per-segment risk ceilings.** `hard_ceiling_pct` is config per segment: ~2% for the US segments, deliberately higher for NSE so the gate chain permits 1 lot instead of rejecting everything. The elevated number is **written into every trade record and shown on the dashboard**, so NSE performance is never read as if it were achieved at professional risk levels.
4. **Capital viability check** at startup: computes median premium/lot across the live watchlist and logs a warning when 1 lot exceeds the ceiling. Warning, not a halt.
5. **Fractional lots remain rejected** — they would break "realistically simulate execution", which is a hard spec requirement.

Expect NSE Stock Options to trade infrequently and NSE Index to hold 1–2 positions. Both segments will produce smaller samples than the US segments, so their promotion gates lean harder on the CI-based criterion than on the raw 100-trade count (§Promotion).

### A3. The validation gate cannot be met on NSE as specified

§Strategy Research Framework: *"Only strategies demonstrating statistically meaningful performance should be eligible for paper trading."* Combined with §Trade Selection's high selectivity, this needs hundreds of historical option trades per strategy to clear.

Here is what the vendors actually give you (verified against their docs, not assumed):

| Data | Upstox (NSE) | LondonStrategicEdge (US) |
|---|---|---|
| Live option chain: LTP, OI, IV, Greeks, volume | ✅ | ✅ |
| Live bid/ask | ✅ (market feed) | ✅ |
| Historical option **OHLCV + OI** | ✅ **but Upstox Plus (paid) only**, via Expired Instruments APIs (launched 13 May 2025); 1/3/5/15/30-min + day | ✅ contract candles, 1-min |
| Historical option **IV / Greeks** | ❌ **not returned** — must be recomputed | ✅ (vendor's own model) |
| Historical option **bid/ask** | ❌ | partial (prints, not quotes) |
| Historical **order flow / prints** | ❌ | ✅ options flow, prints back to **2014** |
| Historical **market depth** | ❌ | ❌ |
| Historical underlying | ✅ candles v3 | ✅ US equities back to **2003**, 14 resolutions |
| Streaming replay from a past timestamp | ❌ | ✅ native in their SDK |

> ### ✅ DECIDED: backtest on underlying OHLCV. No Upstox Plus.
>
> *"We will backtest the strategies or algo on OHLCV data of Stocks or Indices... to know if the algorithm is understanding the market and its direction or not."*
>
> This is a better answer than either option I offered, and it collapses a large amount of scope. Adopted.

Backtesting moves off option contracts entirely and onto the **underlying**, where both vendors have deep, free, reliable history (Upstox candles v3; LSE US equities to 2003). The question the backtest answers is narrowed to the one that actually matters first: *does this algorithm read direction correctly?*

**What this deletes:** the Upstox Plus subscription, the expired-instruments adapter, historical option-chain reconstruction, and historical Greek back-solving as a backtest dependency. Roughly two weeks of work and a recurring bill, gone.

**Validation becomes two tracks with a clean division of labour:**

- **Track A — directional backtest (offline, underlying OHLCV).** Does the algorithm identify direction and timing? Measured in market terms, not rupees: hit rate, MFE/MAE ratio, expectancy in ATR units, **signal lead time** (§C10), and stability across regimes. Runs over years of history on both markets.
- **Track B — shadow (live, real option chains, zero capital).** Does a correct directional call actually make money *buying options* after spread, theta, IV crush and slippage? This is where option economics, contract selection and the depth-derived features get validated. Starts producing evidence the day the recorder is live.

**The one risk this split creates, and how it's handled.** A directional edge on the underlying does *not* guarantee an options-buying edge — a correct call on a move too small or too slow still loses to theta and IV crush. Track A can therefore pass a strategy that Track B will kill.

Mitigation, cheap because the pricing module exists anyway: **a synthetic-option overlay as a second pass on every Track A backtest.** Price a Black-76 option along the backtested underlying path using an IV assumption from the current surface, and report estimated option P&L alongside the directional metrics. It answers "is this move big enough, and fast enough, to beat theta?" — the central question for an options *buying* system — before anything reaches shadow mode. Clearly labelled an estimate; **never counted toward a promotion gate**, which remains Track B's job.

**Two consequences that survive this decision:**

1. **Roughly a third of §Market Analysis Engine is still not backtestable, ever.** Order flow, absorption, bid/ask imbalance and dealer-flow features need depth or prints that no historical NSE source provides. Track B is their only validation path. The feature registry marks them `backtestable=False` so the promotion pipeline routes them correctly (§Feature registry).
2. **The live engine still needs full option pricing** — Greeks on live chains for contract selection, position monitoring and risk. Pricing remains load-bearing; it just stops being a *backtest* dependency.

### A4. Optuna contradicts both "no self-learning" and "avoid curve fitting"

The stack table lists Optuna for "hyperparameter optimization and strategy tuning". Automated parameter search is (a) exactly the self-optimization §AI Loop forbids in-system and (b) the most efficient curve-fitting device ever built, while §Strategy Research Framework says "Avoid curve fitting and over-optimization."

**Resolution I propose:** keep Optuna, fence it hard.

- Runs **only** in an offline research CLI. Never imported by the engine — enforced by an import-linter rule in CI.
- Mandatory **purged, embargoed walk-forward CV**. No single-split optimization.
- Every trial logged to `optimization_runs`. The trial count feeds a **Deflated Sharpe Ratio** so multiple-testing is penalised explicitly rather than ignored.
- Output is a *proposal*, never a deployment. Human promotes.
- Hard cap on tunable parameters per strategy (proposed: 4). Most curve-fitting is parameter-count, not search algorithm.

---

## B. Ambiguities needing a decision

| # | Ambiguity | My default if you don't decide |
|---|---|---|
| B1 | **"US Index Options" is undefined.** SPX/NDX/RUT are European, cash-settled, no early assignment. SPY/QQQ/IWM are *ETF* options: American, physically settled, dividend-driven early exercise. Different pricing model and different settlement. | ~~Treat the segment as **SPX/NDX/RUT (European, cash-settled)**; put SPY/QQQ/IWM in the US **Stock** segment where the American pricing model already lives.~~ **Superseded by ADR 0007**: the data vendor (LSE) carries no SPX/NDX/RUT at all — verified by enumerating its full catalog. User's explicit decision: US Index trades SPY/QQQ/DIA/IWM (index-tracking ETF options) instead, using the American/physically-settled pricing path. |
| B2 | **"NSE Index Options" opportunity set.** Post-SEBI Nov 2024 only one weekly expiry per exchange survives (NSE: NIFTY). BANKNIFTY/FINNIFTY/MIDCPNIFTY are monthly-only. Expiry weekday has changed repeatedly. | Watchlist = NIFTY (weekly + monthly) + BANKNIFTY/FINNIFTY monthly. **All expiry rules read from the exchange instrument master daily — never hardcoded.** |
| B3 | **No reporting currency.** Master Dashboard shows "Overall P&L" and "Combined equity" across ₹ and $ segments. | Per-segment figures always native. Combined figures converted at the **trade-close-date** FX rate (never today's rate), with the rate stored on the record. Reporting currency configurable, default INR. |
| B4 | **"Screenshots or chart references if supported."** | **Chart references, not screenshots.** Store `{instrument, timeframe, t0, t1, overlays[]}` on the trade; the dashboard re-renders from TimescaleDB. Queryable, never rots, and deletes the object-store dependency (§F3). |
| B5 | **Single user or multi-user?** No auth requirement stated, but the API will expose a kill switch and strategy promotion. | Single user. API binds to localhost; optional password + signed session cookie behind a config flag for LAN access. |
| B6 | **What is a "trade" when scaling out?** Spec wants partial exits but stores one entry/exit price. | Trade = one contract position with an append-only fill history. Reported entry/exit are size-weighted averages; every individual fill is preserved. |
| B7 | **"Transaction costs if applicable."** They are very much applicable — on ₹50k capital, costs are a material fraction of edge. | Full cost model per segment, config-driven: NSE (brokerage, STT on sell premium, exchange txn, SEBI turnover, stamp duty, GST on the fee stack); US (per-contract commission, OCC/ORF, SEC/TAF on sells). Seeded with current rates, **verify against a real contract note before trusting backtest P&L.** |
| B8 | **"Emergency risk shutdown" scope.** | Two levels: per-segment breaker (auto, on daily/weekly/DD limits) and a global kill switch (manual, halts all four). Both persisted, both requiring explicit human re-arm. |
| B9 | **Backtest history depth.** | Underlying OHLCV both markets (Upstox candles v3; LSE US equities to 2003). Cap initial research at **3 years** — regime relevance decays, and older NSE data predates the 2024 lot-size and expiry rule changes entirely. |

---

## C. Missing requirements

Things that will break the system in production and appear nowhere in the spec.

- **C1 — Upstox token lifecycle.** ~~Originally written here: Upstox access tokens expire daily and require an interactive OAuth login, making this "the #1 operational failure mode of the whole system."~~ **Corrected during implementation (ADR 0006):** that's true of Upstox's standard OAuth token, but not the product this platform actually uses. Upstox separately offers an **Analytics Token** — long-lived (1 year), read-only, generated once from the Developer Apps dashboard, no daily re-auth, no OAuth flow — which is exactly "the Upstox Analytics Long Term API" SPEC.md names. It covers market data, option chain, historical candles, and websocket; it cannot place orders, which is a free bonus reinforcement of the paper-only guarantee (§11). Remaining need, much smaller than originally scoped: track the token's expiry date (read manually off the dashboard, since Upstox exposes no endpoint to query it) and alert well before the ~1-year mark. See `kairodex.data.upstox.auth.AnalyticsToken`.
- **C2 — Vendor quota management.** LSE explicitly shares one allowance between streaming and download and exposes `GET /vault/usage`. Upstox caps instruments per websocket connection and requests per call (Option Greeks: 50 keys). Needs a quota-aware scheduler, or ingestion will throttle mid-session in ways that look like market data gaps.
- **C3 — Alerting.** Spec has thorough logging and zero notification. Logs nobody reads are not observability. Needs a small alert sink (desktop notification / webhook / email) for: breaker tripped, feed down > N seconds, token expiring, ingestion gap, engine crash.
- **C4 — Data retention and disk budget.** Unbounded option-chain recording fills a laptop disk in weeks. See ARCHITECTURE §Capacity for the tiered plan.
- **C5 — Look-ahead and survivorship discipline.** Not mentioned once, and it is the failure mode that makes backtests lie. Watchlist membership changes; F&O ban lists; delistings; lot-size revisions (NIFTY 25 → 75 mid-history). Every one silently inflates results. Needs: point-in-time `as_of` on every feature, SCD-2 instrument specs so backtests use the lot size *in effect then*, and point-in-time watchlist membership.
- **C6 — Corporate actions for US.** Spec mentions NSE only. US splits create adjusted option contracts (non-standard deliverables, odd strikes). Needs handling or those contracts must be excluded.
- **C7 — Clock discipline.** Spec lists "clock synchronization issues" under Reliability but sets no behaviour. Proposed: engine measures skew between vendor server timestamps and local clock, logs it, and halts entry on skew > 2s.
- **C8 — Restart recovery semantics.** "Recovery after restart" is listed with no definition of correct. Proposed: engine rebuilds all state from the append-only event log + last equity snapshot; ingestion resumes from `max(ts)` per instrument and backfills the gap; positions are re-marked before any new decision is allowed.
- **C9 — Definition of "statistically meaningful".** The central promotion gate is undefined. Proposed concrete gates in ARCHITECTURE §Promotion.
- **C10 — Definition of "early".** §Early Trade Detection is the primary objective and is unmeasurable as written. Proposed: label every signal with a forward outcome (did a ≥1.5× ATR move follow within N bars?) and track **signal lead time** and **MFE capture ratio** as first-class metrics. Without this you cannot tell whether the system's stated primary objective is being met.

---

## D. Technical risks

| Risk | Severity | Mitigation |
|---|---|---|
| ~~No NSE option history~~ → *resolved by the A3 decision* | — | Backtesting runs on underlying OHLCV; option economics validated in shadow mode. No purchase needed. |
| Directional edge doesn't translate to an options-buying edge (theta, IV crush) | **High** | Synthetic-option overlay on every Track A run flags moves too small to monetise; Track B gates on real option P&L before any capital is allocated. |
| NSE segments starved of sample size at ₹50,000 | **High** | CI-based promotion gate instead of raw trade count; affordability-filtered watchlist to maximise tradeable opportunities. Revisit if NSE Stock yields < 5 trades/month for two months. |
| Option-chain write volume overwhelms the DB | **High** | Tiered ingestion (T0/T1/T2) + Timescale compression + retention. ~2M rows/day/market instead of ~58M. See ARCHITECTURE §Capacity. |
| Backtest/live divergence — the classic killer | **High** | **One engine, two clocks.** Same code path for replay and live; a golden test asserts a replayed session reproduces the live session's trades exactly. |
| Vendor lock-in / LSE is a small provider | Medium | All market data behind a `MarketDataProvider` port. Swapping to Polygon/Tradier/ThetaData is an adapter, not a rewrite. |
| Vendor-computed Greeks differ between markets | Medium | Compute all Greeks ourselves, one module, both markets. Store model + inputs for reproducibility. |
| Overfitting via repeated manual iteration | Medium | Deflated Sharpe with honest trial counts; a held-out period never touched during research; shadow-mode results weighted above backtest. |
| Sample size never reaches significance at high selectivity | Medium | Track a running power estimate; report CIs on every metric; treat "not yet significant" as a first-class strategy state rather than a pass. |
| Scope — §Market Analysis Engine lists ~40 concepts | Medium | Feature registry with tiers; ship ~15, each with a declared data requirement and quality flag. The list is a backlog, not a milestone. |
| TA-Lib C dependency breaks Docker builds | Low | Drop it (§F4). |

---

## E. What I'd add that isn't in the spec

1. **Shadow mode** (A3). The missing rung between backtest and paper. Also the only NSE validation path if Plus isn't bought.
2. **Rejected-signal logging.** Store every opportunity the risk engine or scorer *declined*, with the reason. §Trade Selection says "no trade is a valid decision" — then the no-trades must be data too. Half the learning signal in the export bundle will come from these.
3. **Feature registry with data-quality tiers.** Each feature declares its inputs, whether it's exact or proxied, and whether it's backtestable. Makes the 40-item wish list incrementally deliverable and stops proxied features being read as ground truth.
4. **`research_notes` table + import CLI.** Closes the manual loop the spec describes but leaves dangling: external findings get recorded, linked to the strategy versions they caused, and shown on the dashboard.
5. **Capital viability pre-flight** (A2).
6. **Golden replay test.** One recorded session, deterministic expected trades. Catches strategy regressions and engine drift in a single test.

---

## F. Proposed deviations from the specified stack

The spec says to treat itself as authoritative unless a change significantly improves scalability, maintainability, reliability, or performance. These five qualify. Each is a **net deletion of code**, not an addition.

### F1. Drop Backtrader — write the replay loop instead

> **Delegated to me.** *"Backtesting will happen on Stocks or Indices OHLCV... So you decide to drop Backtrader or not."* → **Dropping it.** Reasoning below, re-derived under the new backtest scope rather than carried over.

The OHLCV decision genuinely improves Backtrader's fit — bar-synchronous single-instrument backtesting is exactly what it was built for, and my original "it can't handle option chains" objection no longer applies. So this needed rethinking, not restating.

It still loses, on two grounds:

1. **The duplication argument is untouched, and it was always the decisive one.** Backtrader cannot run the live path. The live engine must work on real option chains with contract selection, risk gates and the fill simulator. Adopting Backtrader means the detectors, the confluence scorer, the feature registry and the `as_of` discipline all get expressed twice — once in Backtrader's `Cerebro`/`Strategy`/`Indicator` idiom, once in ours. The golden "replay reproduces the live session exactly" test, which is the whole guarantee of Principle 1, becomes impossible to write. Backtest/live divergence is the single most common way platforms like this fail.
2. **The replacement got much smaller.** Feeding bars instead of chains through `ReplayClock` is now roughly **200–300 LOC**, not the 600–900 I estimated for chain replay. The bar loop is the one part Backtrader would have contributed, and it is now the cheapest part of the system to own.

→ `kairodex.engine` with a `Clock` port: `LiveClock` and `ReplayClock`. Same orchestrator, same detectors, same risk gates, same code, one engine. LSE's SDK natively supports replay from a past timestamp, which drops straight into this shape.

### F2. Drop Celery — APScheduler + a Postgres job table  ✅ APPROVED

The workload is three shapes, and Celery is a poor fit for the first and overkill for the others:

- Continuous ingestion → **long-lived asyncio processes**, not tasks. Celery actively fights this.
- Periodic rollups/exports → APScheduler in one `jobs` process. ~10 lines.
- Backtests → a `jobs` table + worker pool, or just the CLI initially.

Celery costs a broker protocol, a result backend, worker lifecycle management, and a second config surface, for zero benefit at single-node scale. → Add ARQ or Celery when backtests genuinely need multi-node distribution. Marked in code with the upgrade path.

### F3–F5 — my call, not explicitly answered

You approved F2 and delegated F1. F3–F5 went unanswered, and you did not pick "keep the spec stack literally", so I'm deciding them rather than spending another round: **all three dropped.** Each is a few hours to reverse if you disagree — say so any time.

### F3. Drop MinIO/S3

Its only consumer in the spec is "screenshots or chart references". B4 replaces screenshots with chart *references*, which are better anyway. Export bundles go to a local `./exports/` volume. → Add object storage when you need off-site backup or multi-node, not before.

### F4. Drop TA-Lib

A C dependency that reliably breaks Docker builds, for ~6 indicators (ATR, EMA, RSI, ADX, VWAP, Bollinger) that are ~80 lines of Polars expressions — and which vectorize better *inside* the feature pipeline than as an external call. The spec itself says indicators must never be the sole reason for a trade, so this surface is small by design. → Add `polars-talib` later if the indicator set grows.

### F5. Drop PyFolio; demote QuantStats

PyFolio has been unmaintained since 2020. QuantStats is pandas-only and produces HTML tearsheets — useful offline, wrong for a live dashboard that needs per-segment, per-regime, rolling metrics queryable from the DB. Those metrics are ~150 lines of NumPy that we need in Postgres anyway. → Compute in-house; keep QuantStats for offline tearsheets only.

### Also: compute all Greeks in-house

Not a deletion, a correctness fix. Upstox and LSE each return Greeks from their own models with different rate/dividend/style assumptions — NSE options are all **European**, US equity/ETF options are **American**. Comparing segment performance across vendor-computed Greeks compares two different models. → One `kairodex.pricing` module: Black-76 off the synthetic forward for European (which also sidesteps rate and dividend guessing on NSE), Bjerksund-Stensland for American. Vendor Greeks retained as a cross-check field, never as the source of truth.

### Kept exactly as specified

Python + FastAPI (thin interface layer, no business logic), PostgreSQL + TimescaleDB (earns its keep — see §Capacity), Redis (three real uses: latest-state cache, engine→API pub/sub, single-writer lock), Polars primary / Pandas at library boundaries, NumPy/SciPy/statsmodels, py_vollib as a pricing cross-check, Optuna (fenced per A4), Next.js + TS + Tailwind + shadcn/ui, TradingView Lightweight Charts, REST + WebSocket. Numba only if profiling demands it. C++ not needed — nothing here is latency-critical at paper-trading cadence.

---

## G. Decisions — resolved 2026-08-04

| # | Question | Decision | Source |
|---|---|---|---|
| 1 | NSE paper capital | **Keep ₹50,000** as specified. NSE runs single-position at 6–12% risk; accommodations per A2. | You |
| 2 | Historical option data / Upstox Plus | **Not needed.** Backtest on underlying OHLCV; option economics validated in shadow mode. | You |
| 3a | Backtrader | **Dropped** — delegated to me; reasoning re-derived in F1. | Me, delegated |
| 3b | Celery | **Dropped** → APScheduler + jobs table. | You |
| 3c | MinIO / TA-Lib / PyFolio | **Dropped** — my call, unanswered; reversible. | Me |
| 4 | Build order | **Recorder first.** Minimal status page at P1; dashboards at P6. | You |
| 5 | "US Index Options" | **SPX/NDX/RUT** (European, cash-settled). SPY/QQQ/IWM live in the US Stock segment. | Default, unopposed |
| 6 | Reporting currency | **INR** for combined figures; per-segment always native; conversion at trade-close-date FX. | Default, unopposed |
| 7 | A1 — no model inference in the trading path | **Confirmed.** System is fully deterministic; "AI" is the external human-gated loop. | Default, unopposed |

Decisions 5–7 were proposed as defaults and drew no objection. Say the word if any of them should change — 5 and 7 are cheap to revisit now and expensive later.

**Kept from the spec, unchanged:** Python + FastAPI (interface only), PostgreSQL + TimescaleDB, Redis, Polars/Pandas, NumPy/SciPy/statsmodels, py_vollib as a pricing cross-check, Optuna (fenced offline per A4), QuantStats (offline tearsheets only), Next.js + TS + Tailwind + shadcn/ui, TradingView Lightweight Charts, REST + WebSocket.
