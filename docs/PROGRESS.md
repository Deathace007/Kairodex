# Kairodex — Development Progress

**Last updated:** 2026-08-14 (evening — §20)
**Current phase:** P1 (The Recorder) is done pending the unattended
5-session check (§7). P2 (Pricing & features) is functionally complete
(§8). **P3 (Engine & paper execution) is functionally complete and
subagent-reviewed**: every piece ARCHITECTURE.md §3's engine box names —
risk gate chain, execution simulator, position monitor, contract
selector, orchestrator — is built, tested, deployed as 4
systemd-supervised shadow-mode engine processes (one per segment) on the
VM, live-verified end to end against real NIFTY chain data through to an
actual simulated fill with real pricing and costs, AND independently
reviewed by a subagent that found and (in this session) fixed 6 more real
bugs on top of the ones caught live — see §9c for the live-verification
bugs and §9e for the review-pass bugs (exit fills that could self-cap
toward zero forever, partial exits dropping their own P&L, equity/risk
state never being written at all, sizing able to exceed the premium/
exposure caps, put delta-targeting backwards, and three of six exit
checks being unreachable). **Not yet measured**: P3's own exit criterion
(full lifecycle in shadow mode for 5 sessions) needs real calendar time
to elapse — the clock started at deployment, see §9d.

**P4 (Backtest & validation) is functionally complete**: ReplayClock,
Track A backtest runner over real historical underlying OHLCV (fetched
live from Upstox candles v3 for the whole NSE watchlist, 643 real daily
bars/instrument, 2024-2026), directional metrics (hit rate, MFE/MAE
ratio, expectancy in ATR units + bootstrap CI, signal lead time, MFE
capture ratio, quarterly/regime consistency), a Black-76 synthetic-option
overlay (ESTIMATE-labeled, never a gate), purged/embargoed walk-forward
splits, deflated Sharpe, a held-out-period guard, and the full promotion
state machine + both gate sets (Track A DB-free, Track B against real
trades/fills/equity_snapshots). Live-verified end to end: a real
`kairodex backtest run` against the whole nse_stock watchlist (7,525
resolved signals over ~2.5 years) correctly failed 5 of 7 Track A gates
— the expected, correct outcome for `ReferenceStrategy`, which is
explicitly "not tuned... exists to prove the pipeline wiring, not to
trade well." See §10 for the full account, including **two more
live-affecting bugs found and fixed this pass** (`Fill.spread_bps`/
`slippage_bps` never written despite the columns existing, and a
`sync_watchlist` bug that had every nse_stock/nse_index underlying
double-registered in the ALREADY-DEPLOYED live engine's watchlist since
2026-08-04) — and the golden replay test's honest status: the mechanism
is live-verified against 12 real recorded signals (byte-identical
replay), but no real trade has been TAKEN yet to replay a full
open-to-close lifecycle against, so that specific case remains
unexercised until one occurs.

**P5 (Analytics & export) is functionally complete and subagent-reviewed**:
`kairodex/analytics/` (performance, breakdowns by weekday/session/expiry/
moneyness/vol_regime/regime, daily/weekly/monthly equity rollups) and
`kairodex/export/` (the full ARCHITECTURE.md §14 bundle — manifest with
embedded JSON Schema, trades/trade_events/rejected-signals/equity/
performance/feature-dictionary/data-quality/digest/README — plus
`research_notes` import). Live-verified against **real trades this
session found already open on the VM** — see §11 for how P3's shadow
engine had, without anyone watching for it, taken 3 real trades since
the last session. An independent subagent review found and fixed 2
live-affecting bugs in the already-deployed engine (`trades.avg_exit`
silently `lot_size`x too large, `trades.fees` zeroed to 0 on every close)
plus 8 more findings in this session's own new code — see §11e.

**P6 (API & dashboards) is functionally complete, deployed, and
subagent-reviewed**: FastAPI read layer (every endpoint ARCHITECTURE.md
§15 names), `WS /ws/stream` backed by a real Redis pub/sub fanout (the
first real consumer of the Redis wiring P1 left unbuilt), and a Next.js
master + parameterized-segment dashboard with real TradingView charts.
Deployed as 2 more systemd units (`kairodex-api`, `kairodex-frontend`) —
the full stack is now 9 units. Two genuinely dangerous, previously-inert
safety controls became real this session: the global kill switch (named
in the risk gate chain's own docstring order since P3, never actually
wireable to anything until now) and per-segment manual breaker halts —
both **live-verified by actually engaging them against the real running
engine**: the kill switch produced 20 real `kill_switch`-rejected
signals in the DB before being released, and a manual breaker trip
survived a full engine tick cycle without auto-reverting before being
re-armed. An independent subagent review found and fixed a live
connection-leak in the WS handler (found by inspecting real orphaned
Redis subscribers on the VM) plus 7 more findings. See §12 for the full
account.
**P7 (Strategy build-out & hardening) has started — hardening half
first** (§15). Twenty-six real trades exposed a duplicate instrument
identity that had every open NSE position monitoring a five-day-dead
quote row (one with no stop-loss monitoring at all), and the closed-trade
record showed 92% of all loss coming from trades that never worked even
briefly. Root cause fixed, four evidence-derived exit/entry rules added.
**§17 is the first clean session under all of it** — 5 closed NSE trades,
+Rs 1,591, nothing from the hardening misfiring. It also fixed a
re-entry cooldown that only counted losses (a winning close let the
engine buy the same underlying back on the next tick, which cost Rs 6,023
in one session), and finally populated `signals.forward_outcome` — 21,128
NSE signals resolved against their own forward bars. **That measurement
says the confluence scorer's confidence is anti-predictive**: every
confidence filter makes the forward outcome worse than the unfiltered
baseline. See §17d before tuning any threshold.

**§20 (P7-C) is the current frontier.** The first live session under §19's
rules closed -Rs 2,729 on 10 trades, and the largest loss driver was not
the strategy: `max_premium_pct` (0.35) binds below the risk-budget
notional (0.40x equity) while `exposure_cap_pct` is 0.40, so every trade
asks for 35% of capital, `max_concurrent: 5` is unreachable, and position
size carries **zero** information (`corr(notional, return) = -1.2%` across
115x dispersion). The engine also **stored no features** — 2 rows in
`feature_vectors`, `feature_vector_id` NULL on all 79,209 signals — which
is what made "learn from past decisions" impossible rather than unbuilt.
That is now wired (§20e) and live-verified; `target_delta` moved 0.40 ->
0.50 on a measured cost curve; a feature backfill is running. **Read §20d
before proposing any signal work**: the option costs ~1.28 ATR and the
median signal offers 0.95, so only 42.7% of signals can ever pay for the
instrument expressing them. §20f lists what was tested and rejected.

**§19 (P7-B) remains the reference for the exit ladder and the scorer.**
Confidence being anti-predictive is now confirmed **out-of-sample**, and
it is not the confluence aggregation — *each detector is individually
inverted at its own extremes*. `oi_price_flow` did not reproduce §18c's
edge once scored on the same metric as everything else, and the one
literature-backed replacement hypothesis (intraday momentum) was tested
and rejected before any code was written. **No detector in this system
has demonstrated positive edge.** Three changes shipped on the exit side
instead, where the evidence is real: stop 30%->20%, partial rungs
(0.5,1,2)R -> (2,)R, scratch 90->45 min — one coupled decision, since R
*is* the stop distance. `min_confidence` is retired as a quality filter
and relabelled a volume throttle; the weekly p75 re-siting ritual is
over. **Read §19b and §19c before proposing any scorer work.**
The US restart hazard §17g armed was closed by §18e's halt, not by
tuning; §19a adds a second, independent reason `us_index` cannot trade.

> Read this file first, every session. Update it whenever a phase
> completes, a decision changes, or a command/location changes. Deeper
> reasoning lives in ARCHITECTURE.md, SPEC_REVIEW.md, and docs/adr/ — this
> file is the short version of "where are we and why is it like this."

---

## 1. Decisions that changed the plan

Don't re-litigate these — each overrides SPEC.md or an earlier assumption. Full reasoning is in the linked ADR/section.

| Decision | Why it changed | Detail |
|---|---|---|
| NSE paper capital stays **₹50,000** (spec default) | User's explicit call, kept despite it being too small to properly size NSE Stock Options | ADR 0005 |
| Backtesting runs on **underlying OHLCV only** — no historical option-chain backtesting, no Upstox Plus needed | Option economics validated later in live "shadow mode" instead | ADR 0002 |
| **Backtrader dropped** — one custom engine drives both live and replayed trading | Same code path for live/replay is the only way to guarantee they agree | ADR 0001, 0003 |
| **Celery, MinIO, TA-Lib, PyFolio dropped** | Replaced by APScheduler, local file exports, Polars, in-house metrics | ADR 0004 |
| **Upstox auth = long-lived Analytics Token, not daily OAuth** | Originally designed around daily reauth (flagged as #1 operational risk); corrected once user pointed out the actual product Upstox offers | ADR 0006 |
| **Project renamed `otp` → `kairodex`** mid-P0 | Avoid the OTP/one-time-password acronym collision. Package, CLI, DB user/name, Compose project, env var prefixes all follow | — |
| **All Docker/DB/test execution moved to a remote VM**; laptop is code-editing only | Not in the original architecture — added once user clarified their actual working setup | memory `infra_vm_workflow.md` |
| **T1 watchlist default list is a Claude proposal**, not a strategy call | User explicitly asked for a proposed default rather than specifying symbols | `config/watchlist.yaml` — edit freely before `sync-watchlist` |
| **Status page is a CLI command** (`kairodex status`), not an API endpoint | User's explicit choice — full dashboard/API is P6 scope, a text report is enough to know the recorder is alive | `kairodex/status.py` |
| **Alert delivery (desktop/webhook) deferred** — P1 only logs + `kairodex status` | User's explicit choice — nothing downstream reads a push alert yet | Wire `structlog` + a sink (ARCHITECTURE.md §17) when something needs paging, not pulling |
| **Upstox WS wire format sourced from the vendor's own `.proto`** (fetched from `github.com/upstox/upstox-python`), not guessed from docs prose | Reaction to the LSE field-name mistake below (§6 #2) — same mistake here would poison every recorded quote, not just one test | `src/kairodex/data/upstox/proto/MarketDataFeedV3.proto` |
| **US_INDEX segment trades SPY/QQQ/DIA/IWM (ETF proxies), not SPX/NDX/RUT** | LSE carries no true index options at all (verified live against its full 3,186-underlying catalog — `list_expiries()` for SPX/NDX/RUT returns zero, no error). User's explicit decision, overriding SPEC_REVIEW.md §B1's original SPX/NDX/RUT-only call | ADR 0007; `config/watchlist.yaml`. These are real ETF shares (`InstrumentKind.UNDERLYING`, not `INDEX`) on the American/physically-settled pricing path, not the European/cash-settled one §B1 assumed — matters once P2 pricing lands. Must not also appear in `us_stock` — same option legs, segment would flip depending on iteration order |
| **LSE's date-range `options()` filter is broken for SPY/QQQ specifically** | Live-discovered: unfiltered or `min_dte`/`max_dte`-filtered calls return exactly 5000 rows, all months-old, for these two tickers only (every other tested ticker works). An exact `expiry=` date query returns correct live data | `kairodex/data/lse/client.py`'s `_probe_expiries` — probes exact dates directly when the normal call comes back empty, bounded to 14 days / stops at 2 matches. All four `us_index` constituents live-verified end to end after the fix: real quotes, correct `segment` tag, 0.02% gap rate |
| **Per-tick SEQUENCE_GAP quality flag removed from the WS path** | Live-verified: a 2s expected-interval threshold flagged most of a healthy live book as "gapped," since individual option contracts (especially thin strikes) legitimately go minutes between prints — that's normal, not a feed problem. `feed_health.connected`/`last_message_at` is the real stream-liveness signal | `kairodex/data/recorder.py` — `flag_tick()` still checks STALE/CROSSED_BOOK/ZERO_VOLUME/OUTLIER per tick |
| **Upstox WS "full" mode subscription hard-capped at 2000 keys** | Live-discovered 2026-08-05: NSE's real future-expiry universe for the 22-underlying watchlist is ~7,400 option-contract keys — Upstox's WS silently accepts the oversized subscribe list, reports a normal connection, and then never sends a single message (no error, no rejection frame). Bisected live: 2000 keys streams real ticks in seconds, 3000 produces zero in 25s+. All quotes recorded during the broken window actually came from the T1 REST poll, not WS — masked because the WS periodic-flush heartbeat updates `feed_health.last_message_at` unconditionally, so `kairodex status` showed `connected: yes` the whole time regardless | `kairodex/data/recorder.py`'s `MAX_WS_SUBSCRIBE_KEYS` / `_resolve_ws_keys` — orders by expiry ascending, truncates at the cap, dropping farthest-dated legs first (T1's REST poll still records those, just not at WS cadence) |

---

## 2. Where everything lives

| What | Where |
|---|---|
| **Local repo** (edit code here only) | `/Users/mohanborle/AI_ML/Kairodex` |
| **GitHub remote** | `git@personal:Deathace007/Kairodex.git` (branch `main`), via `personal` SSH host alias — `~/.ssh/config`, key `~/.ssh/id_ed25519_personal` |
| **VM** (all Docker/DB/tests/ingestion run here) | E2E Networks, `ssh -i ~/.ssh/id_ed25519_personal root@164.52.206.92` |
| **VM repo clone** | `/opt/Kairodex` |
| **VM Compose project** | `kairodex` → containers `kairodex-timescaledb-1`, `kairodex-redis-1` |
| **VM `.env`** | `/opt/Kairodex/.env`, mode 600 — transferred via `scp -O` (see §5) |

**Workflow, always:** edit locally → commit → push → SSH to VM → `git pull` → run.

**Also on the VM, in `/opt/`:** an unrelated app `swingpro` (Compose project `infra`). Its containers/images were removed 2026-08-04 to reclaim disk (116GB was build cache); its 8 volumes were left untouched deliberately.

---

## 3. Quick command reference

```bash
# SSH to the VM
ssh -i ~/.ssh/id_ed25519_personal root@164.52.206.92

# On the VM: pull latest code, bring up infra
cd /opt/Kairodex && git pull origin main
source $HOME/.local/bin/env   # puts uv on PATH
docker compose up -d && docker compose ps   # both should be "healthy"

# Migrations
uv run alembic upgrade head && uv run alembic current

# Lint / type-check / test (same locally or on VM)
uv run ruff check . && uv run mypy src/kairodex/ && uv run pytest -q

# One-shot chain pull (P0 exit criterion, still works)
uv run kairodex ingest pull-chain --market nse --underlying "NSE_INDEX|Nifty 50" --expiry YYYY-MM-DD
uv run kairodex ingest pull-chain --market us  --underlying AAPL --expiry YYYY-MM-DD

# P1: seed a market (one-time / whenever the watchlist changes)
uv run kairodex ingest sync-instruments --market nse   # T0: full instrument master -> instruments
uv run kairodex ingest sync-watchlist   --market nse    # T1: seed watchlist_membership from config/watchlist.yaml
uv run kairodex status                                  # per-provider connection state, last message age, gap rate

# P1: the recorder + jobs processes themselves — systemd-supervised, not manual (see §4a)
systemctl status kairodex-ingest-nse kairodex-ingest-us kairodex-jobs
journalctl -u kairodex-ingest-nse -f          # tail live logs (repeat with -us / -jobs)
systemctl restart kairodex-ingest-nse         # e.g. after a code deploy — see below

# Inspect the DB directly
docker exec kairodex-timescaledb-1 psql -U kairodex -d kairodex -c "SELECT count(*) FROM instruments;"
```

**Deploying a code change:** `git pull origin main` alone does *not* pick up a
running process's code. This note used to name only the recorder units, and
that omission bit on 2026-08-12 (§18f): `kairodex-api` ran for six days across
three config-schema changes and served a stale risk config the whole time.
**The full unit list, and what each one cares about:**

| unit(s) | restart after a pull touching |
|---|---|
| `kairodex-ingest-{nse,us}` | `kairodex/data/` |
| `kairodex-jobs` | `kairodex/jobs` |
| `kairodex-engine-{nse_stock,nse_index,us_stock,us_index}` | `kairodex/{engine,strategy,risk,execution,features,pricing}/`, `config/segments/*.yaml` |
| `kairodex-api` | `kairodex/{api,analytics,export,store,streaming}/`, **`config/segments/*.yaml`** |
| `kairodex-frontend` | `frontend/` |

`config/segments/*.yaml` appears twice on purpose: `config.segments.
get_segment_config` is `@lru_cache`d, so a YAML edit reaches **no** running
process until that process restarts.

```bash
# the blunt, always-correct version — every unit, safe while markets are shut
systemctl restart kairodex-{ingest-nse,ingest-us,jobs,api,frontend}
systemctl restart kairodex-engine-{nse_stock,nse_index,us_stock,us_index}
```

Check what is actually running before assuming — start times do not lie:
```bash
for u in $(systemctl list-units 'kairodex-*' --no-legend --no-pager | awk '{print $1}'); do
  printf "%-40s %s\n" "$u" "$(systemctl show "$u" -p ExecMainStartTimestamp --value)"; done
```

NIFTY expiries are Tuesdays, not obvious guesses — `ingest run`/`poll_chain_once` resolve them live via `list_expiries()` (Upstox: `GET /v2/option/contract`; LSE: `options()` with no expiry filter), not a guess or a hardcoded date.

**Local-only is fine for:** `ruff`, `mypy`, `pytest` (no DB dependency — all P1 unit tests use synthetic protobuf/Tick fixtures, not a live DB). **VM only:** anything touching Postgres/Redis/live vendor calls, i.e. every command in the "P1: bring up the recorder" block above.

---

## 4. Completed — P0 (Foundations)

All verified against live systems, not just "should work":

- Repo scaffold: `uv`, `src/kairodex` layout, ruff + mypy (strict) + import-linter — all clean.
- Docker Compose: TimescaleDB + Redis, healthy on the VM.
- Schema (migration `354672f0f639`): reference tables + market-data hypertables (compression on `option_quotes`, 30-day retention on `market_depth`). Applied on the VM.
- **Upstox adapter**: Analytics Token auth, instrument master (live-verified: 34,738 instruments, correct segment classification), option chain, historical candles, rate limiting. Live-tested: real 226-contract NIFTY chain → Timescale.
- **LSE adapter**: thin wrapper over the official `lse-data` client. Live-tested: real 126-contract AAPL chain → Timescale (after the field-name fix in §5).
- `MarketDataProvider` port shared by both adapters.
- Minimal ingest/upsert layer (`kairodex/data/ingest.py`) — the real tiered recorder is P1.
- CLI (`kairodex ingest pull-chain`) proving the path end to end.
- 6 ADRs in `docs/adr/` — see §1 for the ones that matter most.

---

## 4a. Completed — P1 (The Recorder)

Built and locally lint/type/test-clean (`ruff`, `mypy --strict`, `pytest` —
28 tests, all DB-free: synthetic protobuf `FeedResponse` and `lse.Tick`
fixtures, not live payloads).

**Self-review before deploying** caught a real bug: `ws_stream_loop` used
`asyncio.wait_for(anext(stream), timeout=...)` as a periodic-flush timer,
but a timeout cancels the pending `anext()`, which tears down the
WebSocket connection instead of just flushing — every quiet period >3s
would have reconnected from scratch. Fixed by running the flush as an
independent task against its own session, never interrupting the tick
consumer. Also fixed: the Upstox subscribe message now matches the
vendor's own example exactly (binary frame, not text).

**Live-verified against the VM, 2026-08-04** (US/LSE — market was open;
NSE/Upstox still needs a market-hours pass, see §7): `sync-instruments`
pulled 34,738 NSE + 3,186 US instruments; `sync-watchlist` matched 20
nse_stock + 2 nse_index + 20 us_stock (after fixing two stale symbols, see
§1); `ingest run --market us` connected, streamed real ticks, and
`kairodex status` showed `connected: yes` with quotes landing in
`option_quotes` within seconds. Two more things live data caught (§1):
**LSE has no SPX/NDX/RUT options at all** — resolved via ADR 0007,
`us_index` now trades SPY/QQQ/DIA/IWM — and the WS path's SEQUENCE_GAP
flag used too tight a per-contract threshold and was removed from that path.

- **Schema**: `feed_health` table (migration `8ed0be22cd84`), `instruments.underlying_symbol` column (migration `85cff23c02d8`, fixes a P0 gap — `InstrumentRecord.underlying_symbol` was parsed but silently dropped on write).
- **T0/T1 seeding**: `kairodex ingest sync-instruments` (full instrument master), `kairodex ingest sync-watchlist` (seeds `watchlist_membership` from `config/watchlist.yaml`).
- **Quality flagging** (`kairodex/data/quality.py`): staleness, crossed book, zero-volume, outlier, sequence-gap → `IntFlag` bitmask. Wired into the REST chain path (`store_chain_snapshot`, all 5 checks) and the WS path (STALE/CROSSED_BOOK/ZERO_VOLUME/OUTLIER only — see above for why SEQUENCE_GAP was dropped there).
- **Upstox WS feed** (`kairodex/data/upstox/feed.py`): real `MarketDataFeedV3.proto` fetched verbatim from `github.com/upstox/upstox-python` (not hand-transcribed), compiled via `grpcio-tools` (`uv run python -m grpc_tools.protoc ...`, see the `.proto` file's header for the exact command), decode logic pinned by `tests/unit/test_upstox_feed.py` against synthetic messages. Wire protocol matches the vendor's own published example verbatim; **field values still need a live NSE market-hours check** (§7).
- **LSE WS feed**: wired directly onto the installed `lse-data==0.14.0` client's real `subscribe_options()` + `stream_async()` — **live-verified**, streamed real AAPL/TSLA/etc. option ticks into `option_quotes`.
- **`list_expiries()`** added to `MarketDataProvider`: Upstox via `GET /v2/option/contract`, LSE via `options()` with no expiry filter — **live-verified** (AAPL returned 19 real future expiries; SPX/NDX/RUT correctly returned empty, no error).
- **Batched writer** (`kairodex/data/ingest.py`): `write_option_quotes_batch`/`write_underlying_bars_batch`, multi-row `INSERT ... ON CONFLICT DO NOTHING` rather than Timescale `COPY` (ARCHITECTURE.md §7 names COPY — swap in if batch-insert latency ever becomes the bottleneck at T1's ~2M rows/day/market).
- **Recorder loop** (`kairodex/data/recorder.py`, `kairodex ingest run --market {nse,us}`): T1 REST poll (60s) + WS stream running concurrently, restart recovery (backfills missing `underlying_bars` via REST on startup — **option-quote history can't be backfilled, no vendor sells it, ADR 0002** — recovery for quotes just means resuming), feed_health heartbeat.
- **`kairodex jobs`**: APScheduler, daily Upstox token-expiry check (+ once at startup).
- **`kairodex status`**: text report — connection state, last message age, subscribed count, quota, 24h gap rate, last error — per provider. **Live-verified** against the US run above.
- Redis latest-state/pub-sub (ARCHITECTURE.md §7) explicitly **not built** — no consumer exists until P3 (engine) or P6 (API WS fanout); building it now would be untestable against nothing.
- No compose services added for `ingest`/`jobs` — there's no `Dockerfile` and the established pattern (per this file's own command reference) runs `kairodex` processes as bare `uv run` commands on the VM, only DB/Redis are containerized.
- **Process supervision: systemd**, decided and deployed 2026-08-05. Three units (`kairodex-ingest-nse`, `kairodex-ingest-us`, `kairodex-jobs`) in `/etc/systemd/system/` on the VM (not checked into the repo — VM-local config, same as `.env`), `Restart=always`, `WantedBy=multi-user.target` (survives reboot; docker's own `restart: unless-stopped` on the compose services means DB/Redis come back first). Output goes to journald (`journalctl -u <unit> -f`), replacing the ad-hoc `nohup ... > file.log` pattern used during live verification — that pattern block-buffered under file redirection and made `tail -f` show nothing for minutes at a time; journald doesn't have that problem. Not reboot-tested (didn't want to bounce a VM with live positions/data mid-session for a self-check) — the config itself (`enabled`, `WantedBy=multi-user.target`) is the standard, trusted mechanism, so this is a reasonable risk to accept rather than test destructively.

## 5. Not done yet (explicitly out of P1 scope)

- No pricing module (Greeks/IV) — P2. No engine/strategy/risk/execution — P3. No backtest — P4. No API/dashboards — P6.
- `config/segments/*.yaml` (per-segment risk config) doesn't exist yet — deferred to P3.
- T2 (focus-tier, 1–5s cadence) has DB/writer support (`tier` column, quality pipeline) but no automatic promotion logic — ARCHITECTURE.md §6 says promotion is scanner-driven, and the scanner doesn't exist until P3.
- Redis latest-state/pub-sub, alert delivery (desktop/webhook), and `ingest`/`jobs` compose services — see §4a's last two bullets.

## 6. Implementation bugs found and fixed (not decisions — just gotchas)

1. **`.gitignore`'s `data/` pattern swallowed `src/kairodex/data/`** (the entire vendor-adapter package), being unanchored. Fixed to `/data/`. Always `git status` after adding a broad ignore pattern.
2. **LSE chain field names were guessed wrong** (`type`/`volume` vs. real `contract_type`/`volume_today`) — written without a test key. Caused a Postgres unique-constraint violation on first live test. Fixed once real credentials allowed verification.
3. **Local `.venv` broke after the repo folder was renamed** (`OptionTradingSystem` → `Kairodex`) — venv shebangs bake in absolute paths. Fixed by deleting and re-running `uv sync`. If local commands give "bad interpreter" after moving the repo, this is why.
4. **`scp` fails on the VM** — no SFTP subsystem in sshd. Use `scp -O` (legacy protocol).
5. **P0's `upsert_instrument` silently dropped `InstrumentRecord.underlying_symbol`** — parsed from the vendor payload but never written to a column. Not caught by tests since nothing read it back. Fixed in P1 (migration `85cff23c02d8`) once T1 chain polling needed it to resolve expiries.
6. **`ws_stream_loop`'s periodic flush used `asyncio.wait_for(anext(stream), timeout=...)`** — caught in self-review before deploying, not live. Cancelling a pending `anext()` on the WS async generator tears the connection down instead of just flushing. Fixed by running the flush as an independent task. See §4a.
7. **`config/watchlist.yaml`'s TATAMOTORS / "Nifty Bank" don't match live Upstox symbols** — TATAMOTORS isn't a currently-listed NSE symbol (2025 demerger); Bank Nifty's real `trading_symbol` is `BANKNIFTY`. `sync-watchlist`'s miss-reporting caught both; fixed by using `M&M` and `BANKNIFTY`.
8. **WS SEQUENCE_GAP flag used a 2s per-contract threshold** — flagged most of a healthy live options book as "gapped," since individual contracts (thin strikes especially) legitimately go minutes between prints. Removed from the WS path; see §1 and §4a.
9. **LSE's `options()` date-range filter is broken for SPY/QQQ** — returns a stale, capped 5000-row page regardless of `min_dte`/`max_dte`; every other tested ticker (DIA, IWM, AAPL, TSLA) works correctly unfiltered. Exact `expiry=` queries return correct live data for SPY/QQQ too. Fixed with a bounded exact-date probe fallback; see §1.
10. **Upstox WS "full" mode has an undocumented ~2000-3000-key subscription ceiling** — see §1. `kairodex status` gave no hint anything was wrong (`connected: yes`, fresh `last message`) because the flush heartbeat fires regardless of whether any tick was actually received; only cross-checking `feed_health.subscribed_count` staying at 0 for several minutes, then bisecting live with a throwaway script, exposed it. **Lesson for future vendor integrations: a "connected" flag that's set independent of real data flow is a false-positive risk — prefer deriving liveness from `subscribed_count > 0` or an actual message counter, not a timer that always fires.**
11. **Two `underlying_symbol` spellings for the same NIFTY 50 index** ("NIFTY" — Upstox's own raw per-contract field, 1790 legs/18 expiries across the full instrument master — vs "Nifty 50" — this codebase's watchlist display label, 436 legs/2 expiries, written by `poll_chain_once`/`store_chain_snapshot`) are both live in `instruments`/`option_quotes` right now. Not yet root-caused or fixed — noticed during the NSE pass above but doesn't block P1's exit criterion (each spelling's legs are internally consistent, no data corruption, just a possible duplicate-identity/double-counting risk for anything that groups by `underlying_symbol` later, e.g. P2/P3 feature aggregation). Worth resolving before P2's feature registry groups by underlying.

---

## 7. Next up — process supervision, then start P2

**NSE/Upstox live-verified 2026-08-05** during market hours (started ~11:56
IST): `kairodex ingest run --market nse` connected, `sync-instruments`
(35,057 instruments) and `sync-watchlist` (20 nse_stock + 2 nse_index, 0
missed) both clean. Found and fixed a real bug along the way — Upstox WS
silently drops the entire subscription once the key list is too large (§1,
§6 #10) — the watchlist's true universe (~7,400 keys) blew way past it with
no error, so all quotes during that window were actually coming from the
T1 REST poll, not WS. Bisected live, capped at 2000 keys
(`MAX_WS_SUBSCRIBE_KEYS`), redeployed, re-verified: `subscribed_count`
climbing past 1000+ real instruments, **0.00% gap rate**, real per-tick
timestamps (hundreds of distinct `ts` per underlying per minute, not the
REST poll's flat 60s cadence). Field-value spot check against
`kairodex/data/upstox/feed.py`'s mapping (§6 #2's class of bug): `ltp`,
`bid`/`ask`/sizes, `volume`, `oi`, `delta`/`gamma`/`theta`/`vega` all
populate correctly and match plausible real values (e.g. delta→1.0 on deep
ITM legs); `rho` is `NULL` on every single NSE row observed (thousands) —
consistent with the code's existing "0.0 treated as absent" design (see
`_to_decimal` in feed.py), reads as Upstox just not sending rho, not a
parsing bug. One open item, not yet root-caused: two different
`underlying_symbol` spellings for the Nifty 50 index in the DB (§6 #11) —
worth resolving before P2 groups features by underlying.

**US_INDEX is resolved and fully live-verified** (ADR 0007) — SPY/QQQ/DIA/IWM
all streaming correctly-tagged quotes on the VM, including the SPY/QQQ
expiry-discovery fix (§6 #9). Nothing further needed here.

**Exit criterion** (ARCHITECTURE.md §19, unchanged): 5 consecutive trading sessions recorded, < 0.5% gap rate on T1, clean restart mid-session with no data loss. `kairodex status`'s gap-rate line is the number to watch — now STALE-driven only (see §1), so it should actually mean something. Both markets now individually verified inside target (US/LSE 0.02%, NSE/Upstox 0.00% post-fix), and process supervision is now live (§4a) — the 5-session clock is running unattended as of 2026-08-05 12:31 IST. Nobody has watched it complete yet; check `systemctl status kairodex-ingest-{nse,us}` / `kairodex status` on a future session and confirm 5 sessions have actually elapsed clean before calling P1 formally done.

P2 (Pricing & features) starts now in parallel — see §8. It doesn't depend on the exit criterion (pricing math is pure functions, DB-free to build and test); P1 just needs to keep quietly running underneath.

---

## 8. P2 — Pricing & features (2026-08-05)

### 8a. Pricing — `kairodex/pricing/` (DB-free, 87 tests)

- **`black76.py`** — European price + analytic Greeks, off a forward (not spot) per ARCHITECTURE.md §8. Validated exactly against `py_vollib.black` (added as a **dev-only** dependency — `py-vollib`, pulls in `scipy`/`pandas` transitively; test-time validation only, never imported by runtime code) across ITM/ATM/OTM × short/long-dated × calls/puts. Every price and all 5 Greeks match to `1e-6`.
- **`forward.py`** — put-call parity forward derivation (`from_put_call_parity`) and a cost-of-carry helper for the American side (`implied_cost_of_carry`). Round-trip tested.
- **`iv_solve.py`** — Brent's method (hand-implemented — stdlib has no root-finder and scipy wasn't worth adding as a runtime dependency for one function), model-agnostic (`sigma -> price` closure, works for both black76 and bjerksund). Simplified Brenner-Subrahmanyam initial guess rather than the full Jäckel "Let's Be Rational" approximation — Brent doesn't need a good guess to be correct, only Newton does. Fails closed (`None`) on an unbracketed target or an unevaluable bracket endpoint. Round-trip tested (generate price at known σ, solve back, recover σ).
- **`greeks.py`** — central finite-difference Greeks for pricers with no closed form (the American path). Validated by running black76's own price function through it and confirming it recovers black76's analytic Greeks.
- **`bjerksund.py`** — **deviates from the spec.** ARCHITECTURE.md §8 names Bjerksund-Stensland (2002) closed-form for the American path; this ships a Cox-Ross-Rubinstein binomial tree instead. Reason: BS2002's exercise-boundary calculation needs a bivariate normal CDF (~15 magic constants) that's real risk to reproduce wrong from memory on a money-path formula with nothing on hand to check it against. ARCHITECTURE.md's own stated accuracy ceiling for BS2002 is "upgrade to CRR binomial if deep-ITM accuracy matters" — this ships that upgrade path as the baseline (a few ms/quote slower, every line checkable by hand). Validated against no-arbitrage properties (American ≥ European ≥ intrinsic, American call = European call when no dividend, monotonicity, convergence). **Found and fixed a real bug via this testing**: the CRR risk-neutral probability goes outside `[0, 1]` at low vol relative to the time-step size — unguarded, it silently returned `3e+105` for a 1bp-vol call. Now raises `ValueError`; `iv_solve.solve` catches that at the bracket endpoints and returns `None`.

### 8b. Feature registry — `kairodex/features/` (43 more tests, live-verified on the VM)

`@register` decorator + `Tier`/`Fidelity`/`Quality` + `FeatureContext` (`registry.py`/`types.py`) — `FeatureContext` is pure data (no DB session), so every feature's math is DB-free-testable the same way P1's synthetic-Tick tests already work. `compute_all()` isolates a raising/missing-input feature to a `MISSING` quality entry rather than aborting every other feature for the same tick.

**18 registered features**, covering all 15 ARCHITECTURE.md §9 launch-set concepts (ATR and realized vol, IV rank and percentile, and OI change and PCR each bundle two genuinely distinct numbers — registered separately rather than forced into one blended scalar): `atr`, `realized_vol`, `volatility_regime` · `trend_state_strength`, `vwap_position`, `opening_range_position`, `volume_profile_poc_distance`, `price_acceptance` · `relative_strength_vs_index`, `index_correlation` · `iv_rank`, `iv_percentile`, `iv_skew`, `term_structure` · `oi_pcr`, `oi_change`, `net_gamma_exposure`, `liquidity_score`. Every expected test value is either hand-worked in the test's own docstring or cross-checked against an independent reference (stdlib `statistics.correlation`, or a standalone reference loop written separately from the production helper) — not eyeballed for plausibility.

`feature_vectors` (ARCHITECTURE.md §5.3, migration `5256d032cc30`): JSONB `values`/`quality` per `(segment, instrument_id, as_of, registry_version)`, Timescale hypertable on `as_of`. **Deviates from the doc's literal `id bigserial PRIMARY KEY`** — a lone `id` doesn't include the partitioning column, which `create_hypertable` rejects — using the spec's own `UNIQUE(...)` columns as the real composite PK instead (see `FeatureVector`'s docstring in `store/models.py`).

`loader.py` builds a `FeatureContext` from the DB for one (underlying, as_of) — the one file that touches a session, deliberately separate from `compute/*.py`. `index_bars`/`iv_history`/`session_open_ts` are left for the caller to fill in (which index is "the" benchmark, where historical IV rollups come from are real decisions, not invented here). `store.py` is the `feature_vectors` read/write path, with a `compute_and_store` convenience wiring registry + loader together.

**Live-verified 2026-08-05 against real recorded NIFTY data on the VM**: `loader.build_context` pulled 837 real underlying bars and a real 2-expiry/436-leg option chain; `registry.compute_all` produced 10 real, plausible feature values (e.g. `realized_vol≈0.385`, `liquidity_score≈0.96`) and correctly reported the other 8 as `MISSING` (their optional inputs weren't supplied in the smoke test); `store.compute_and_store` wrote a row and `read_feature_vector` read back an exact match. **Found and fixed three real bugs this pass, none reachable by local DB-free testing:**

1. **Migration tried to `CREATE TYPE segment_enum` a second time.** `segment_enum` already existed (from `354672f0f639`); the generic `sa.Enum(..., create_type=False)` didn't stop it — `sa.Enum` gets adapted into a dialect-specific impl at DDL-compile time and doesn't reliably carry `create_type` through that adaptation. Fixed by using `postgresql.ENUM(..., create_type=False)` directly.
2. **`feature_vectors.id` had no default at all.** `autoincrement=True` only auto-attaches a sequence for a single-column integer primary key; `id` here isn't part of the (composite) PK, so it silently got no default — any insert omitting `id` would violate `NOT NULL`. Fixed with `sa.Identity()` (a real Postgres `GENERATED BY DEFAULT AS IDENTITY`), both in the migration and the ORM model. Both migration bugs required downgrading the empty, just-applied table and reapplying — safe since nothing had written to it yet.
3. **The upsert silently wrote garbage into the `values` column.** `stmt.excluded.values` (attribute access) resolved to the Python object's own `.values()` method — because the column is literally named `values` — instead of a SQL column reference; SQLAlchemy then tried to bind that *method object* as a JSONB literal and failed to serialize it (`event_ts`/`quality` worked fine, since those names don't collide with anything). Fixed with subscript access, `stmt.excluded["values"]`, applied to all three `SET` clause entries for consistency.

### 8c. Known gaps, not fixed this session (scoped deliberately, not overlooked)

- **`option_quotes.delta/gamma/theta/vega/rho` hold vendor Greeks, not ours.** `Tick.iv` is also never written (`option_quote_row` only sets `vendor_iv`) — `iv.py`'s `_iv()` helper already prefers `iv`, falling back to `vendor_iv`, so it'll start using our own values automatically the moment this is fixed, no follow-up change needed there. The fix itself (renaming the five vendor columns to `vendor_delta` etc., freeing the unprefixed names, per SPEC_REVIEW.md's explicit "vendor Greeks retained as a cross-check field, never the source of truth") isn't done — it touches the live P1 write path on a running recorder, worth doing deliberately, not as a drive-by.
- **`Instrument.underlying_id` is never populated by any code path.** `loader.load_chain` joins on the `underlying_symbol` string instead — the same workaround `recorder.py`'s `_resolve_ws_keys` already uses, and thus inherits the same fragility flagged in §6 #11 (the NIFTY/"Nifty 50" duplicate-spelling issue).
- **Wiring `compute_and_store` into the live recorder** — nothing calls it from `kairodex/data/recorder.py` yet. ARCHITECTURE.md §8 frames pricing (and by extension features) as serving "the live engine... on demand" — i.e. P3's job, not a P1 ingest-time write. Revisit if the registry ever needs continuously-updated stored history before P3 exists.
- **P2's stated exit criteria aren't formally measured**: "our Greeks match vendor Greeks within tolerance" needs pricing wired to real option_quotes rows (cheap once useful, no consumer yet); "a feature computed live is bit-identical to the same feature computed in replay" **cannot be measured at all yet** — there is no replay clock until P3's `ReplayClock` exists. `FeatureContext` was designed so this holds by construction (pure function of its input data, no hidden clock/IO reads) but that's an architectural argument, not a passing test — don't call it verified until P3 can actually run both paths and diff them.

**Reverified in a later session (2026-08-05):** clean local (`ruff`/`mypy`/124 tests) and VM (identical, migration at head) checks, plus a fresh independent live run — different `as_of`, different bar count (777 vs. 837, real market time passing between runs) — reproducing the same result: pricing sane (round-tripped IV solve exact), full `loader → registry → store → read-back` pipeline exact match, two distinct `as_of` rows now correctly persisted. Nothing regressed.

---

## 9. P3 — Engine & paper execution (started 2026-08-05)

**Scoped deliberately narrow.** ARCHITECTURE.md's own estimate for all of P3 (clock abstraction, orchestrator, strategy framework, risk engine, execution simulator, one reference strategy per segment) is ~3 weeks — too much for one slice, and this codebase's own established rule (stated in `store/models.py`'s docstring, followed again in P2's scoping) is to land schema and code with the phase that actually consumes it, not ahead of a real caller. This session builds exactly what's needed to make **Principle 1 in force where it already can be** (`core/clock.py`'s `LiveClock` — built in P0, nothing to add: `ReplayClock` is explicitly P4's, per that file's own docstring) and **Principle 2 real** ("the event log is the truth") — nothing else.

**Built, tested, and live-verified on the VM:**

- **Migration `app_role_separation`**: creates `kairodex_app`, a real non-superuser role the app runtime connects as. This mattered immediately: the *only* existing DB role, `kairodex`, is a superuser (`\du` confirmed it live) — `REVOKE` is a complete no-op against a superuser, so the next migration's append-only guarantee would have been pure theater without this existing first. `store/base.py`'s `get_engine()` now prefers `Settings.app_database_url` over `database_url`, falling back cleanly when unset (nothing breaks locally/in CI without it configured). `ALTER DEFAULT PRIVILEGES` covers every table, present and future, with one deliberate exception carved out by the next migration.
- **Migration `trading_tables`** (ARCHITECTURE.md §5.4, the minimum needed for `trade_events`' own FKs to resolve): `strategies`, `strategy_promotions`, `signals`, `trades`, `trade_events`, plus the `paper_trades` view. Two deliberate deviations from the doc's literal SQL, documented in `Signal`/`Trade`'s docstrings in `store/models.py`: `signals.feature_vector_id` and `trades.run_id` are soft references (no FK) — `feature_vectors` has no single-column unique key to reference (the same Timescale hypertable constraint from P2 §8), and `backtest_runs` doesn't exist yet (P4). **Deferred, not built**: `orders`, `fills`, `position_marks`, `equity_snapshots`, `risk_state` — these belong to the execution simulator and risk engine, neither of which exists yet to write them.
- **`kairodex/engine/event_log.py`**: SHA-256 hash chain **per trade** (not global — concurrent trades never contend on one chain). `verify_events` (pure, DB-free) is split from `append_event`/`verify_chain` (DB-touching) — same pattern as `kairodex.features`' `compute/*.py` vs. `loader.py` split. 11 tests cover every tampering shape that matters: edited payload, deleted event, reordered events, a forged event with a self-consistent hash that still breaks the *next* link, a chain not rooted at genesis — not just the happy path.

**Live verification went further than "does the migration apply"** — the actual security property was tested directly, twice:

1. Connected to Postgres **as `kairodex_app`** and attempted `UPDATE`/`DELETE` on real `trade_events` rows (created via the real `append_event` path, not synthetic SQL): both attempts returned `permission denied for table trade_events`. The rows were confirmed byte-for-byte unchanged afterward.
2. Then, **as the admin `kairodex` role** (which `REVOKE` cannot touch — Postgres superusers bypass every grant check), directly edited a real event's payload — simulating "someone with DB admin access, not going through the app, tampers with history." `event_log.verify_chain` on that trade correctly returned `False`. This is the defense-in-depth claim in the module's own docstring, not just asserted — actually exercised.

Both migration bugs along the way were caught live and fixed the same session: the enum-recreation bug from P2 §8 recurred on the two new enum types (`side_enum`/`strategy_status`) for the identical root cause, and a naming mismatch (`APP_DB_PASSWORD` vs. a wrongly-prefixed `KAIRODEX_APP_DB_PASSWORD`) failed the migration closed rather than silently creating a role with an empty password.

**Deployed for real**, not just verified in isolation: all three systemd-supervised recorder processes (`kairodex-ingest-nse`, `kairodex-ingest-us`, `kairodex-jobs`) were restarted to pick up `kairodex_app`, one at a time, each confirmed healthy afterward (`kairodex status`, and a direct count of fresh `option_quotes` rows) before moving to the next. P1's recorder now runs least-privilege too, not just P3's new tables.

### 9a. Strategy framework — `kairodex/strategy/` (28 tests, live-verified)

`MarketContext` (thin wrapper: P2's `FeatureContext` + its computed feature values — detectors never call the registry themselves), `Evidence`/`DetectorFamily` (the four from §10: structure/flow/volatility/relative_strength — exactly these, no fifth), and `ConfluenceScorer` implementing "requiring >= N independent detector families to agree... single-family agreement can never fire, whatever the score" **literally**: one weighted vote per family (not per detector — two detectors agreeing within the same family is correlation, not confluence), a side only wins by strictly outnumbering the other's agreeing families, confidence is the weighted mean of the agreeing evidence's magnitude.

**Four detectors, deliberately one per family** — a strategy built from only 1-2 families could never exercise the single-family-never-fires property meaningfully: `trend_structure` (from `trend_state_strength`), `oi_price_flow` (from `oi_change` + price direction, the standard NSE long-buildup/short-buildup/short-covering/long-unwinding convention), `iv_skew_sentiment` (from `iv_skew`), `relative_strength` (from `relative_strength_vs_index`). Each is a `tanh`-bounded translation of an already-computed P2 feature; scaling constants are documented first-pass estimates (ponytail-flagged in each detector's own docstring), not calibrated against real historical data — that's P4's job. `ReferenceStrategy` bundles all four behind the `Strategy` protocol's `evaluate()` — `manage()`/`Position`/`ExitDecision` deliberately not implemented, that's the position monitor's territory (§11/§12), not built yet.

**Found and fixed a real logic bug via its own hand-verified tests**: the flow detector's conviction multiplier compared OI's sign to price's sign, which doesn't match the standard convention documented in the same file's own docstring table — both "buildup" rows (long buildup *and* short buildup) are OI increasing, regardless of price direction. The bug graded a real short-buildup case (price down, OI up — textbook bearish, full conviction) as merely half-conviction. Fixed to check OI direction alone.

**Live-verified twice against real NIFTY data**, chained directly onto P2's already-verified `loader`/`registry` pipeline:

1. With only `underlying_bars`/`chain` populated (the loader's unconditional output): 2 of 4 detectors correctly fired (the other 2 correctly abstained — their inputs, `oi_change`/`relative_strength_vs_index`, need `prior_chain`/`index_bars`, which the loader deliberately leaves to the caller). The two that fired disagreed in direction and were both weak; the scorer correctly produced no signal.
2. With `prior_as_of` and a (smoke-test-only, self-referential) `index_bars` supplied: all 4 detectors fired, producing a real result — `direction=BUY, confidence=0.075`. The low confidence is itself a correct outcome: the underlying evidence really was weak (scores of +0.028 and +0.122), and a third family (volatility) actively disagreed; the scorer didn't inflate a marginal case into false conviction, and a neutral (`0.000`) relative-strength reading was correctly excluded from *both* sides rather than forced into one.

### 9b. Risk engine — `kairodex/risk/` + `config/segments/*.yaml` (54 tests)

`config/segments/*.yaml`, one per segment: `capital`/`base_risk_pct`/`hard_ceiling_pct`/`max_premium_pct` are exactly ARCHITECTURE.md §11's own table (asserted against it directly in `tests/unit/test_segment_config.py`). Daily/weekly loss limits, max drawdown, exposure cap, liquidity floor, and re-entry cooldown aren't given exact numbers in the doc ("config, not hardcoded" is the instruction, not a value) — round, documented, freely-adjustable first-pass defaults.

`sizing.py` implements the doc's formula exactly (`risk_budget = equity * base_risk_pct * risk_multiplier(...)`, `lots = floor(risk_budget / (stop_distance * lot_size))`); `risk_multiplier`'s curve shape (scale up on profit, down on drawdown, damped by consecutive-loss streak) is this session's own design since the doc explicitly delegates it ("a config-driven curve," no numbers given).

`gates.py`: the 11 gates in the doc's literal order, each independently testable, the chain stopping at the first denial. Anti-revenge (no re-entry within N minutes of a loss) folded into `correlation_cluster_gate` since the doc's numbered order doesn't name it separately. `event_blackout_gate`/`session_window_gate` are honest placeholders — no earnings/macro calendar or populated `trading_calendar` exists yet.

**Caught a real dead-code bug via the gate chain's own tests**: `exposure_gate` running before `capital_available_gate` mathematically guarantees `premium <= uncommitted capital` by the time the latter runs (since `exposure_cap_pct <= 1` always) — its own separate uncommitted-capital check was permanently unreachable. Removed rather than left as false defense-in-depth.

`loader.py` (the one DB-touching file, same split pattern as everywhere else) builds a real `AccountState` — including a pragmatic fallback for `session_open` (checks `trading_calendar` first, falls back to an approximate hardcoded NSE/US session window since no vendor holiday sync exists yet) so live verification during actual market hours is possible at all before that real integration is built.

### 9c. Execution simulator, position monitor, contract selector, orchestrator

**`kairodex/execution/`** (38 tests): `fills.py` implements ARCHITECTURE.md §12's fill model as a pure function — stale quote / incomplete chain / crossed book rejected before pricing anything, then spread-too-wide / size-vs-OI rejected on policy, only then a fill price (`mid ± k*half_spread`) and possibly-partial quantity. `costs.py`: NSE and US cost models seeded from published rates, repeating the doc's own caveat verbatim ("verify against a real contract note before trusting backtest P&L"). `simulator.py`: `SimulatedBroker` + `ShadowLogger`, the exactly-two `ExecutionPort` implementations §11 calls for — deliberately stateless (attempt-count tracking lives in the DB, not a long-lived Python object, this codebase's usual principle). Caught and fixed a real interface bug (`compute_nse_costs`/`compute_us_costs` had different signatures despite needing to be interchangeable through one `cost_model` slot) and reverted a real testability regression before shipping (a module-level `PAPER_ONLY` assertion would have made `DATABASE_URL` a transitive import-time requirement for a pure function — removed; the same guarantee already exists via `Settings.kairodex_paper_only`'s own field validator).

**`kairodex/engine/monitor.py`** (23 tests): `Position`/`ExitDecision` (missing from the original strategy-framework session since they didn't exist yet) plus every exit rule from §11's controls list — stop-loss, trailing stop (ratchets up without exiting until price actually falls through the trailed level), profit target, partial exits at R-multiples (lowest untaken target first, each fraction computed against the *current* remaining size), time-based exit (theta guard), event-based exit. `evaluate_exits` runs all six in priority order: risk protection before mandatory exits before opportunistic ones.

**`kairodex/strategy/contract_selector.py`** (9 tests): turns a directional signal into one actual option leg — option type from direction, filtered to an expiry window, filtered by the affordability constraint (§11: "the contract selector treats it as a constraint rather than a preference"), delta-targeted among what's left, with a spot-distance fallback when a leg has no vendor delta.

**`kairodex/engine/orchestrator.py`**: pure glue — `run_entry_tick` (features → `ReferenceStrategy.evaluate` → `ConfluenceScorer` → contract selection → risk gate chain → sizing → execution → DB writes) and `run_exit_tick` (latest mark → reconstruct `Position` from the trade row → `evaluate_exits` → stop ratchet / partial or full exit / no action, always writing a `position_marks` row). Contract selection deliberately runs *before* the risk gate chain, not after — documented in the module docstring why (liquidity/capital-available gates need a specific candidate's data to evaluate at all; the component diagram's box order is schematic, not a literal constraint).

Three bugs caught and fixed through careful review before any live test: `select_contract`'s affordability filter was being called with `lot_size=1` while the real trade used the actual per-market lot size (would have silently passed genuinely-unaffordable contracts); a candidate's expiry was being patched onto a frozen dataclass via `object.__setattr__` after construction (replaced with passing it into the constructor); `ExecutionResult` was dropping `spread_bps`/`slippage_bps` that `FillOutcome` already computed (Fill rows would have had those columns permanently `NULL` — extended it, with a regression test). Two more caught by mypy while wiring the live loop: `run_entry_tick`/`run_exit_tick` were typed to accept the concrete `SimulatedBroker` class instead of the `ExecutionPort` protocol (would have made passing `ShadowLogger` — the actual shadow-mode default — a type error); `Strategy.id` as a plain Protocol attribute demanded write access that `ReferenceStrategy`'s frozen dataclass field can't structurally offer (fixed with a read-only `@property`).

**`kairodex/engine/live_loop.py`** + `kairodex engine --segment X` CLI command: the process ARCHITECTURE.md §3 names, shadow mode by default (`ShadowLogger`, zero capital — matches P3's own exit criterion), one `strategies` row ensured per segment, looping the watchlist through `run_entry_tick` then every open trade through `run_exit_tick` every 60s.

**Live-verified on the VM, after market hours (both NSE and US closed at test time)** — this mattered: it meant testing the fail-safe paths, not just the happy path, and caught the single most important bug of this session:

- `account.session_open` correctly evaluated `False` (real NSE hours had passed) — the session gate's fallback logic works.
- With `session_open` overridden for testing (the *only* thing genuinely blocked by the clock), a full `run_entry_tick` pass against real NIFTY data flowed through cleanly, confluence legitimately not firing that tick (consistent with §9a's earlier finding that most moments don't produce strong evidence) — correct behavior, not a bug.
- Testing contract selection through execution directly against real chain data (226 real candidates) surfaced a **critical bug**: `TradeProposal.chain_complete` was wired to `front.complete`, a `ChainSnapshot` property requiring `expected_count` — which `kairodex.features.loader.load_chain` never sets, because that field is P1's "did one atomic REST fetch return everything" concept, and `load_chain` instead reconstructs a snapshot leg-by-leg from each instrument's own latest quote, where "complete" isn't a meaningful idea the same way. `front.complete` was therefore unconditionally `False` for **every chain this pipeline has ever built**, which the liquidity gate correctly rejected as `INCOMPLETE_CHAIN` every single time — **no signal could ever have reached execution, in any market condition, ever**, until this was caught. Fixed to `chain_complete=True` unconditionally at that call site (genuine incompleteness already surfaces as `select_contract` simply failing to find a candidate), verified live immediately after the fix.
- With the fix live, the gate chain correctly passed, sizing computed correctly (5 lots for a near-ATM call at ₹90.10, 602 lots for a deep-OTM put at ₹0.775 — mathematically consistent given the sizing formula, and a real emergent behavior worth watching: cheap far-OTM premium sizes very large under a percentage-of-premium stop, not flagged as a bug but worth revisiting once real P&L data exists), and — using a timestamp near the quote's own (since real quotes were genuinely stale after-hours by the time of testing) — a **complete simulated fill**: 5 contracts at ₹90.16, real cost breakdown (brokerage/regulatory fees/taxes), spread_bps=22.2, slippage_bps=6.7. The SELL/put side correctly rejected with `SPREAD_TOO_WIDE` (a real, appropriately-protective rejection — a 0.775-premium contract's spread is naturally huge in relative terms).

**Deployed as 4 systemd-supervised shadow-mode services** (`kairodex-engine-{nse_stock,nse_index,us_stock,us_index}`), enabled at boot, `Restart=always`, started one at a time (`nse_index` first, watched for several minutes before starting the rest). All 4 confirmed `active (running)`, no errors in their logs, `strategies` rows confirmed created in the DB. The full stack is now 7 systemd units: 2 ingest, 1 jobs, 4 engine.

### 9d. What's left before P3 can be called fully done

- **The exit criterion itself isn't measured yet.** "Full lifecycle runs in shadow mode for 5 sessions; PnL identity and risk-ceiling property tests pass" needs real calendar time (the clock started at deployment, 2026-08-05) and, ideally, a couple of real trades actually completing (open → monitored → closed) to exercise the full lifecycle, not just signals being evaluated. Check back after several real trading sessions have elapsed on both markets.
- **`instrument_specs`' real lot sizes aren't wired in** — `orchestrator.py` uses a fixed 25 (NSE) / 100 (US) default per market, not the real per-underlying SCD-2 lot size P0 already stores. Flagged inline with a `ponytail:` comment at its one call site.
- **Real per-underlying correlation clustering** doesn't exist — `correlation_cluster_gate` uses same-underlying-only as a first-pass proxy (documented in its own docstring).

### 9e. Subagent review pass — 6 real bugs found and fixed (275 tests)

Per the explicit instruction to "verify everything in detail and use sub agents so you dont miss anything" before calling P3 done, a subagent reviewed the full P3 diff (`6995543^..HEAD`) independently — re-derived the risk/sizing/fill math by hand, traced `orchestrator.py`'s wiring field-by-field, cross-checked every "bug found and fixed" claim in §9b/§9c against the actual current code (all confirmed real). It found 6 genuine defects, all fixed here, most severe first:

1. **Exit fills could self-cap toward zero, forever.** `run_exit_tick`'s exit-side `QuoteSnapshot` fabricated `bid_sz=ask_sz=trade.qty_lots` (the position's own remaining size) instead of using the real quoted depth — fed straight into `compute_fill`'s `partial_fill_alpha` cap (max fillable = 25% of book size), so every exit tick could fill at most 25% of *whatever remained*, asymptotically shrinking toward (and eventually hitting) zero fillable quantity — a stop-loss could get permanently stuck open. Also `quote_ts=now` silently disabled the `STALE_QUOTE` check on every exit. Fixed: `_latest_mark` renamed `_latest_quote`, returns the full `OptionQuote` row; the exit `QuoteSnapshot` now uses its real `bid`/`ask`/`bid_sz`/`ask_sz`/`ts`, exactly like the entry side already did.
2. **Partial exits dropped their own P&L.** `gross_pnl`/`net_pnl` were only ever computed on the leg that happened to fully close a trade, against the *entire original* `premium_paid` — every earlier partial exit's proceeds were silently discarded, and `avg_exit` recorded only the last leg's price, not a qty-weighted average. Fixed: each leg now realizes P&L against its own proportional share of the entry cost basis (`avg_entry * filled_qty * lot_size`) and entry fees (proven by induction that `trade.fees * filled_qty / qty_before_exit` gives the correct per-leg share at every step since `trade.fees` is itself decremented the same way each time), accumulated into running `gross_pnl`/`net_pnl` totals; `avg_exit` is now a true qty-weighted average across all legs, tracked via `cum_exit_qty`/`cum_exit_value` in `risk_params` until the position fully closes.
3. **`equity_snapshots`/`risk_state` were never written.** `risk.loader.build_account_state` reads both tables (with defaults when empty), but nothing ever inserted into either — `equity`/`high_water_mark` stayed pinned at the hardcoded 50000 default forever, and `daily_pnl`/`weekly_pnl`/`consecutive_losses`/`breaker_status` stayed at zero/ARMED forever. That made `daily_loss_gate`, `weekly_loss_gate`, `drawdown_throttle_gate`, `breaker_gate`, and `risk_multiplier`'s own profit/loss scaling structurally inert — accepted risk, not enforced risk. Fixed: new `kairodex/risk/accounting.py` (`update_equity_and_risk_state`), recomputed from `trades`/`position_marks` each call (cash = capital + cumulative realized P&L, unrealized = sum of latest marks on open positions, daily/weekly P&L = closed-today/closed-this-ISO-week, consecutive losses = trailing-loss streak), including a minimal auto-trip breaker (parks `breaker_status="TRIPPED"` on a daily/weekly/drawdown breach, un-trips automatically once the breaching figure itself resets — deliberately stateless, no separate manual-reset flow for a paper system). Wired into `live_loop.run_segment`, called once per tick cycle after entries/exits.
4. **Sizing could commit more premium than the caps allow.** `risk.gates.exposure_gate`/`capital_available_gate` only ever checked *one lot's* premium, before `sizing.size_position` decided the real (possibly much larger) lot count — since `stop_distance` is a fraction of premium (30%, `_DEFAULT_STOP_LOSS_PCT`), risk-budget sizing alone could size up a lot count whose *total* premium is several multiples of what the pre-sizing gate checked, verified numerically to exceed `max_premium_pct` for the US segments at `risk_multiplier` > 1.0. Fixed: `size_position` now takes `premium_per_lot`/`total_exposure` and caps the lot count against `max_premium_pct` and `exposure_cap_pct` directly, using the real premium this trade would commit — not just what one lot's pre-check happened to pass.
5. **Put delta-targeting was backwards.** `contract_selector.select_contract`'s delta-distance ranking compared a put's signed delta (negative, by this codebase's own convention — see `features.compute.iv._iv_near_delta`'s `-target_delta` call for puts) directly against the bare positive `target_delta`, which made the *weakest* (near-zero-delta) put always look "closest" to target — silently selecting the worst put on every bearish signal. Fixed: sign the target to match `option_type` (`-target_delta` for puts). The only pre-existing put test had a single candidate and couldn't have caught this; added `test_picks_closest_delta_to_target_among_affordable_puts` with three puts at different deltas as a regression test.
6. **Profit-target/R-multiple-partial/time exits were unreachable.** `run_exit_tick`'s `Position(...)` construction never populated `profit_target`/`r_multiple_targets`/`max_holding_secs` (all defaulted to `None`/`()`/`None`), so three of `evaluate_exits`'s six checks — fully implemented and unit-tested in `monitor.py` — could never fire in the live engine; only stop-loss/trailing-stop/event-exit were ever reachable. `partial_exits_taken` was being *written* to `risk_params` on each partial exit but never *read back*, meaning a re-triggered R-multiple check would have had no memory of what was already taken. Fixed: entry-time defaults (`_DEFAULT_PROFIT_TARGET_PCT=1.0`, `_DEFAULT_R_MULTIPLE_TARGETS=(1.0, 2.0)`, `_DEFAULT_MAX_HOLDING_SECS=3 days`) are now written into `risk_params` at fill time (`_record_fill`) and read back into every `Position` `run_exit_tick` builds, including `partial_exits_taken`.

All 6 fixes are in `kairodex/risk/sizing.py`, `kairodex/risk/accounting.py` (new), `kairodex/strategy/contract_selector.py`, and `kairodex/engine/orchestrator.py`/`live_loop.py`. `accounting.py` follows the established DB-touching `loader.py` pattern (no local unit test, same as `risk/loader.py`/`features/loader.py` — verified live on the VM instead, per `CLAUDE.md`'s local-is-DB-free-only rule). 4 new local unit tests added (sizing premium/exposure caps + `INVALID_PREMIUM`, put delta-targeting regression); 275 tests passing, ruff/mypy clean.

- **`Strategy.manage()` still isn't wired into the orchestrator** — `run_exit_tick` calls `evaluate_exits` directly rather than through `strategy.manage()`, unaffected by this pass since the reference strategy's exits don't need feature context. Revisit once a strategy actually wants feature-aware exits.

---

## 10. P4 — Backtest & validation (2026-08-05)

Same session as P3, continuing per the user's explicit instruction to complete P4 in full, with the same "constant review" discipline. Scope, per ARCHITECTURE.md §13: Track A only — directional accuracy on underlying OHLCV, no option-chain backtesting (ADR/SPEC_REVIEW A3's decision, "option economics validated later in live shadow mode instead," which P3 already does).

### 10a. DB-free core — `kairodex/backtest/` (59 tests)

`clock.py`: `ReplayClock`, the same `Clock` Protocol (`.now()`) as `LiveClock` — Principle 1's "one engine, two clocks" was already true of `kairodex.engine.orchestrator` before this session (its functions take `now` as a plain parameter, never call `datetime.now()` internally), so this is a small, literal piece, not a redesign.

`resolve.py`: pure forward-outcome resolution — walks a signal's direction/entry/ATR against the underlying's own subsequent bars to a synthetic stop/target/time exit, in ATR units (the doc's own expectancy unit). Ambiguous same-bar stop+target hits resolve to the stop (conservative — daily bars can't know which happened first intrabar).

`metrics.py`: the full directional metrics table — hit rate, break-even hit rate (from the strategy's own realized win/loss profile via `return_atr`, not a fixed R assumption), MFE/MAE ratio (aggregate `sum/sum`, not mean-of-ratios), expectancy in ATR units + bootstrap 95% CI, signal lead time (SPEC_REVIEW C10, "how much of the move came after the signal"), MFE capture ratio, quarterly/regime consistency.

`synthetic_options.py`: Black-76 overlay along the backtested path at a flat IV assumption, ATM at entry (no chain to delta-target against — a documented simplification, not a precision claim), `ESTIMATE`-labeled, deliberately never read by `promotion.py`'s gates.

`validation.py`: purged/embargoed walk-forward splits (a signal is purged from train if its OWN forward-resolution window reaches within the embargo of test_start, not just its `ts` — real purging, not just a date cut), walk-forward efficiency (mean OOS/IS expectancy ratio), deflated Sharpe (Bailey & López de Prado 2014, the Gaussian non-skew/kurtosis-adjusted variant — a documented simplification, upgrade once there's enough trade history to trust a skew/kurtosis estimate), the held-out-final-period config guard.

`promotion.py`: the `DRAFT -> BACKTESTED -> VALIDATED -> SHADOW -> PAPER_SMALL -> PAPER_FULL` state machine (`RETIRED` reachable from the last three) + Track A gate checking (every threshold from ARCHITECTURE.md §10's own table, DB-free) + Track B gate checking (the one DB-touching function here, against real `trades`/`fills`/`equity_snapshots` — sample size, profit factor, max drawdown, slippage realism vs. the fill model's own assumed ratio, directional agreement with Track A within 1 SE).

### 10b. DB-touching runner — `kairodex/backtest/runner.py`, `history.py`, `backtest_runs`

`history.py`: backfills `underlying_bars` via each vendor's existing `MarketDataProvider.bars()` (Upstox candles v3 / LSE vault — both already built in P0/P1 for restart-recovery gap-fill, no new vendor code needed).

`runner.py`: `run_backtest` steps a segment's whole underlying bar history bar-by-bar, building a `FeatureContext` with `chain=[]` at each step (Track A has no option chain), runs the **same** `Strategy`/`ConfluenceScorer` the live engine uses, resolves any signal via `resolve.py` against the underlying's own subsequent bars. Loads its whole bar range up front (one query, not one per bar) — a backtest steps through thousands of bars where the live engine steps through one tick per cycle.

`BacktestRun` (new table + migration `295470f137eb`): one row per run, an aggregate `metrics` JSONB summary (ARCHITECTURE.md §5.4's own schema) — not a row per signal, since `signals` carries no `run_id` to distinguish backtest from live and reusing it would conflate the two. `trades.run_id` is now a real FK to `backtest_runs` (was a documented soft reference since the table didn't exist yet).

CLI: `kairodex backtest fetch-history --market {nse,us} --start ... --end ...` and `kairodex backtest run --segment ... --from ... --to ...` (pools the whole segment's watchlist into one strategy-level Track A result, prints every gate's pass/fail, writes a `backtest_runs` row).

### 10c. Live verification — real vendor history, a real backtest run, and the golden replay guarantee

`fetch-history` against real Upstox candles v3, 2024-01-01 to 2026-08-05: **643 real daily bars** for all 20 nse_stock symbols plus Nifty 50 and BANKNIFTY — no errors, no gaps worth noting.

`backtest run --segment nse_stock --from 2024-01-01 --to 2026-06-01` against that real data: **7,525 resolved signals** across 20 underlyings (after fixing the watchlist bug below — see the corrected run). Track A gate results, exactly as expected for `ReferenceStrategy` ("not tuned or backtested; exists to prove the pipeline wiring"):

```
PASS sample_size:            7525 resolved signals (need >= 200)
PASS directional_hit_rate:   hit_rate=0.3741, break_even=0.3719  (barely clears — no real edge, as expected)
FAIL expectancy:              0.0055 ATR, CI (-0.024, 0.037) — doesn't exclude zero
FAIL mfe_mae_ratio:           1.11 (need >= 1.5)
FAIL signal_lead_time:        0.50 (need >= 0.6)
FAIL consistency:             5/10 quarters positive, 1 regime positive (need 3/4+ and >= 2)
PASS deflated_sharpe:         0.0042 at 1 trial (need > 0)
FAIL walk_forward_efficiency: -2.23 (need >= 0.5)
overall: not ready
```

This is the *correct* outcome — a strategy assembled to exercise the pipeline, not to trade well, correctly fails 5 of 7 gates. The mechanism discriminating real signal quality (not rubber-stamping) is itself the thing being proven here.

**Two more live-affecting bugs found and fixed during this verification pass:**

1. **`Fill.spread_bps`/`slippage_bps` columns existed but were never written.** `ExecutionResult` already carries them correctly (P3's subagent-review fix), but neither `_record_fill` (entry) nor the exit-side `Fill(...)` construction in `orchestrator.py` ever forwarded them — every `Fill` row ever written had these columns permanently `NULL`, which would have silently broken Track B's slippage-realism gate (built this session) the moment real shadow trades existed. Fixed: both call sites now pass `execution.spread_bps`/`execution.slippage_bps` through.
2. **`sync_watchlist` created duplicate active memberships — a real, currently-live bug, not just a backtest artifact.** Caught because `backtest run`'s per-underlying signal counts were exactly double what they should have been (15,050 instead of 7,525). Root cause: `sync_watchlist`'s idempotency check only looked for a `WatchlistMembership` row with `valid_from == today`; this deployment had `sync-watchlist` run on both 2026-08-04 and 2026-08-05, and each run correctly found "no row for today" and inserted a *new* row — but never closed the earlier row's `valid_to` (still `infinity`), so both read as "currently valid" together. **`watchlist_instruments()` is what `live_loop.run_segment` iterates every tick** — this means the already-deployed live engine had been double-evaluating (and could have double-entered) every `nse_stock`/`nse_index` underlying on every tick since the second sync. Fixed `sync_watchlist` to check for *any* currently-open membership (`valid_to = infinity`, any `valid_from`), not specifically today's; cleaned up the 22 already-corrupted rows directly on the VM (kept the earliest `valid_from` per duplicated pair, deleted the rest) — verified via `psql`, 0 duplicates remain across all 4 segments. Verified the fix live: re-ran `sync-watchlist` after deploying it, confirmed no new duplicates; re-ran the backtest, got the correct halved (7,525) signal count.

**Track B** (`evaluate_track_b`) live-verified against real (currently near-empty) shadow data: correctly reports `FAIL sample_size` (0 shadow trades), `FAIL profit_factor` (no closed trades), `PASS max_drawdown` (0.0, legitimately true with nothing open yet), `FAIL slippage_realism`/`directional_agreement` (nothing to assess) — no crash, graceful degradation exactly as designed.

**Golden replay test — honest status, not faked.** ARCHITECTURE.md §13/§18 calls for `tests/replay/` holding a recorded live session, asserting replay reproduces its trades exactly. The mechanism this protects (Principle 1: the same orchestrator, driven by a different clock/data source, must produce identical decisions) is **live-verified against real recorded production data**: 12 real `signals` rows from today's actual live-engine run (spanning both real reject stages that have occurred — `session_window`/`OUTSIDE_SESSION_WINDOW` and `contract_selection`/`NO_CANDIDATES_IN_EXPIRY_WINDOW`) were re-run through `run_entry_tick` with their original `now` timestamp against the same DB, and **all 12 reproduced byte-identical decisions** (same `reject_stage`/`reject_reason`/taken-or-not). What's genuinely not yet possible, and not faked: **zero trades have been TAKEN yet** (0 rows in `trades`, confirmed via `psql` — 2,697 real signals so far, all `REJECTED`), so the specific case the doc's own wording emphasizes — "reproduces its trades exactly" — has no real trade to replay against. A committed `tests/replay/test_*.py` pytest file was deliberately not added: it would need a real DB connection to be meaningful (this repo has no testcontainers/DB-fixture infrastructure, and `testpaths = ["tests"]` means anything placed under `tests/replay/` would be collected and run locally by default, which would either hang or fail against CLAUDE.md's DB-free local rule). This is the same real, undeniable data dependency flagged before P4 started; check back once the live/shadow engines have actually closed a trade.

### 10d. Local test suite state

`kairodex/backtest/` (59 tests) + the `sync_watchlist`/`Fill` fixes (no new local tests — both are DB-touching, same untested-loader/orchestrator precedent). Full suite: 334 tests passing, ruff/mypy clean, locally and on the VM.

### 10e. What's left / known gaps (deliberate, not overlooked)

- **The golden replay test's "reproduces trades exactly" case** — needs a real TAKEN trade to exist first (see §10c). Not fabricatable honestly; revisit once one closes.
- **`ReferenceStrategy` itself has no real edge** (5/7 Track A gates failed, correctly) — expected; it exists to prove pipeline wiring, not to trade. A real strategy build-out is P7 scope.
- **Track B's `evaluate_track_b` has no shadow trade data to score yet** — same root cause as the above; the mechanism is verified, the real numbers aren't populated yet.
- **`optimization_runs` (the doc's named source for deflated Sharpe's "honest trial count") doesn't exist as a table** — `n_trials` is approximated here as `count(backtest_runs) for this strategy_id + 1`, a defensible proxy (every backtest run against a strategy is itself a trial), but not literally the doc's named table. Revisit if hyperparameter-sweep-style optimization runs are ever built (Optuna is explicitly out of the live path per this repo's own import-linter contract).

### 10f. Subagent review pass — 4 findings, all fixed (337 tests)

Same process as §9e, applied to the full P4 diff. A subagent independently re-derived `resolve.py`'s directional math, `metrics.py`'s formulas, `validation.py`'s purge-boundary logic and deflated Sharpe formula, and `runner.py`'s bar-window slicing for lookahead — confirmed all correct, no CONFIRMED-severity findings. It found 4 lower-severity (PLAUSIBLE) issues, all fixed:

1. **`relative_strength_vs_index` had no length-alignment guard**, unlike its sibling `index_correlation` (which already checks `len(u_bars) != len(i_bars)`) — harmless before this session (every caller left `index_bars=[]`, so it always returned `None`), but P4's `index_bars` wiring made it live; a real data gap or holiday-calendar mismatch between an underlying and its benchmark could have silently compared misaligned windows. Fixed: added the same length guard.
2. **`resolve_forward_outcome` would crash on `max_holding_bars <= 0`** — unreachable via any current caller, but a negative value would have silently sliced from the *end* of `forward_bars` (Python's `list[:-n]`) rather than truncating the lookahead window, and zero would crash the `TIME`-exit branch's `bars[-1]`. Fixed with an explicit guard.
3. **`walk_forward_splits`'s test-window bounds were inclusive on both ends for every fold** — a signal landing exactly on the shared boundary between fold `i`'s `test_end` and fold `i+1`'s `test_start` (the same instant, since folds are contiguous) could double-count into both folds' test sets. Fixed: exclusive upper bound except on the true last fold.
4. **`sync_watchlist`'s duplicate-membership fix (§10c) checked `valid_to == date.max`**, while `watchlist_instruments` (the read path) checks `valid_to >= today` — the two conditions happen to coincide today (nothing sets a finite `valid_to` yet), but the mismatch would have silently reintroduced the exact duplicate-membership bug class the moment a "scheduled removal" feature is ever added. Fixed to match the read path's own condition exactly.

3 new tests (relative-strength alignment regression, zero/negative `max_holding_bars`); 337 tests passing, ruff/mypy clean.

---

## 11. P5 — Analytics & export (2026-08-06)

Scope per ARCHITECTURE.md §14/§19: "All metrics + breakdowns, rollups,
export bundle with JSON Schema, digest, `research_notes` import." Exit
criterion is "one month of shadow data exported and reviewed end-to-end
in Claude Code, findings imported back" — see §11d for the honest status
of that (there isn't a month of data yet; the mechanism is fully built
and live-verified against the real data that does exist).

### 11a. Real data existed before this session even started

Checking the VM before building anything (docs/PROGRESS.md's own "read
this first" habit, applied to the DB, not just the file) found P3's
shadow engine had, unattended, **taken 3 real trades** since the last
session — 1 `nse_stock`, 2 `nse_index`, all still open — and
`equity_snapshots` had real per-tick data for all 4 segments going back
to whenever P3's subagent-review fix (§9e #3, `risk/accounting.py`) was
deployed. This matters for §11d below: P5 isn't being verified against
an empty database.

### 11b. `kairodex/analytics/` (23 tests)

Same split as every other package: `types.py` (`TradeRecord` — one
closed-or-open trade flattened from `trades`+`instruments`+
`position_marks`; `EquityPoint`/`EquityCurveStats`/`RollupPoint`),
`performance.py` (win rate, profit factor, expectancy, avg R-multiple,
avg win/loss, equity curve stats — all DB-free, pure functions),
`breakdowns.py` (group `TradeRecord`s by weekday/session/expiry/
moneyness/vol_regime/regime and run `performance.summarize` on each
group), `rollups.py` (daily/weekly/monthly equity OHLC-style rollup),
`loader.py` (the one DB-touching file).

**`TradeRecord.r_multiple`** is a pure price ratio,
`(avg_exit - avg_entry) / (avg_entry - initial_stop_price)` — deliberately
carries no quantity anywhere, so it's unaffected by partial exits
changing a position's size over its life (unlike, say, summing per-leg
R's weighted by lot count). `initial_stop_price` comes from
`Trade.risk_params`, the same JSONB blob P3's exit-side already writes.

**`mfe`/`mae`** are computed from `position_marks.unrealized` at query
time (`MAX`/`MIN` per trade), not written back onto `trades` by the
engine — `position_marks`' own docstring already says it "powers MFE/
MAE," this is that promise finally cashed in.

### 11c. `kairodex/export/` — the bundle builder (12 tests, + live)

Pydantic models (`export/models.py`) define every bundle file's shape
once; `bundle.py` serializes real data *through* those same models (so
what's written can never silently drift from what it claims to be) and
`Model.model_json_schema()` supplies the "JSON Schema" the roadmap names
— embedded in `manifest.json` rather than a hand-maintained parallel
`.schema.json` file, which could say something different from what the
code actually writes and nothing would catch it.

`build_bundle` writes: `manifest.json` (schema_version, window, sha256 of
every other file, `code_sha` via `git rev-parse HEAD`, which strategies
actually signalled in the window, embedded schemas), `trades.jsonl`,
`trade_events.jsonl` (the full per-trade event log, Principle 2's source
of truth), `signals_rejected.jsonl`, `equity.csv` (raw snapshots),
`performance.json` (overall + every breakdown + equity curve + daily
rollup), `feature_dictionary.json` (every registered feature's own
docstring as its description — reusing P2's existing documentation
rather than writing a second copy that could disagree with the code),
`data_quality.json` (gap rate, incomplete chain snapshots, quotes
missing our own Greeks — all scoped to the segment and window, see
§11e #3), `digest.md` (token-budgeted, meant to paste into a fresh Claude
Code session), `README.md` (generated, names every known caveat — see
the file itself for the full list, it's long enough that duplicating it
here would drift).

`kairodex/export/research_import.py` + `kairodex research import-notes`:
the return half of the loop. A `FindingsImport` pydantic model validates
a hand-authored `findings.json` (only `findings` itself is required) and
writes a `research_notes` row.

### 11d. Live verification — real bundles, real research_notes row, honest gap

`kairodex analytics report --segment nse_index` / `nse_stock` / `us_stock`
against the VM's real data: correct trade counts, `None` for win_rate/
profit_factor (correctly — nothing's closed yet), real equity figures
(`current=51527.75`, `max_drawdown=2.49%`), real weekday/session/expiry
breakdowns. `kairodex export --segment nse_index --from 2026-08-01 --to
2026-08-06` wrote a real bundle: 2 real trades, 36 real trade_events,
1177 real rejected signals, 1002 real equity points, correct sha256 per
file (spot-checked against `sha256sum` directly), correct `code_sha`,
correct `strategy_versions`. `kairodex research import-notes` wrote a
real `research_notes` row (`note_id=1`), confirmed via `psql`, including
the least-privilege check from P3's own precedent: `kairodex_app` has
exactly `INSERT/SELECT/UPDATE/DELETE` on the new table (the
`app_role_separation` migration's `ALTER DEFAULT PRIVILEGES` covered it
automatically, sequence included).

**Honest gap, not fixed this session**: the exit criterion's "one month
of shadow data" hasn't elapsed — the 3 real trades are still open (§11a),
so `win_rate`/`profit_factor`/R-multiple/every breakdown's P&L column are
all correctly `None`/zero rather than fabricated. The mechanism is fully
built, tested, and live-verified against every kind of real data that
currently exists (open trades, rejected signals, equity snapshots); what
it hasn't been exercised against yet is a closed trade or a month of
history. Revisit once real calendar time (and a closed trade or two) has
passed — same honest-gap pattern as P3 §9d and P4 §10c/e.

### 11e. Subagent review pass — 10 findings, all fixed (380 tests)

Per the same "verify everything in detail with subagents" discipline as
P3/P4, an independent subagent reviewed the full P5 diff — re-derived
`r_multiple`/`profit_factor`/`win_rate`/`expectancy` by hand, traced
`orchestrator.py`'s context_entry/context_exit wiring field-by-field
against the live engine, executed real VM values through every formula,
and cross-checked every "found and fixed" claim in this file against the
actual current code. Two findings were **live-affecting bugs in the
already-deployed shadow engine** (present since P3, only surfaced because
P5 was the first thing to actually read the columns), the rest were in
this session's own new code:

1. **`trades.avg_exit` was `lot_size`x too large.** `run_exit_tick`
   accumulated `cum_exit_value` in money (`price * qty * lot_size`) but
   `cum_exit_qty` in bare lots, so `avg_exit = cum_exit_value /
   cum_exit_qty` came out scaled by `lot_size` — silently correct-looking
   (a plausible-sized number) but wrong, and every `r_multiple` computed
   from it would have been nonsense the moment any of the 3 real open
   trades closed. Fixed: divide by `cum_exit_qty * trade.lot_size`.
2. **`trades.fees` was zeroed to exactly 0 on every full close.** The
   per-leg P&L split reused `trade.fees` itself as a shrinking "remaining
   entry fees to allocate across legs" figure — correct for that one
   internal purpose, wrong for the column's actual meaning (total fees
   paid), so a closed trade always reported `fees=0` regardless of what
   was genuinely paid. Fixed: entry-fee allocation now tracks its own
   `remaining_entry_fees` in `risk_params`; `trade.fees` is a pure
   running total (entry fee, seeded at fill time, plus every exit's own
   fee) that only ever grows.
3. **`data_quality.json` wasn't scoped to the segment being exported** —
   `chain_snapshots_total`/`incomplete` and `option_quotes_missing_own_
   greeks` counted the *entire* database regardless of `--segment`, so an
   `nse_stock` and a `us_stock` bundle for the same window reported
   identical numbers. Fixed: `option_quotes` scoped via a real join to
   `Instrument.segment` (set at ingest time for option legs); chain
   snapshots scoped via "was this underlying ever a member of this
   segment's watchlist" (an approximation, not point-in-time-exact —
   documented in the bundle's own README). Re-verified live: an
   `nse_index` bundle and a `us_stock` bundle for the same window now
   report genuinely different numbers (6,274 vs. 17,414 chain snapshots).
4. **`status.gap_rate`'s new `until` parameter was dead** — the commit
   that added it said it was so `export/bundle.py` could reuse the
   function instead of a second copy of the same query, but the bundle
   code had its own duplicate query anyway. Fixed to actually call
   `gap_rate(..., until=to)`.
5. **`--to` on `export`/`analytics report` silently excluded the given
   date** — both convert `--to` to midnight UTC and every loader filters
   `< to`, so `--to <today>` dropped every trade/signal from today
   entirely (the review caught this making all 3 real VM trades vanish
   from its own test bundle). Fixed: both commands now treat `--to` as
   inclusive (bump to midnight of the day after internally).
6. **Two tests didn't test what their names claimed**, and `bundle.py`
   (350+ lines) had zero tests. A "round trip" test never parsed JSON
   back into a model; an "unaffected by size" R-multiple test had no
   size parameter to vary. Fixed both for real, added
   `tests/unit/export/test_bundle.py` covering every DB-free helper
   (`_trade_export`, `_summary_export`, `_write_equity_csv`,
   `_sha256_file`, `_feature_dictionary`).
7. **A crashed re-export into the same bundle directory could leave a
   stale `manifest.json`** next to freshly-rewritten content files (the
   directory name is deterministic — `bundle_<segment>_<from>_<to>` — so
   re-running into it is the normal case, not an edge case). Fixed:
   delete any existing `manifest.json` before writing anything new, so a
   partial bundle has no manifest instead of a wrong one.
8. **`breakdowns._session_bucket`'s US window was fixed EDT-only UTC**
   (13:30-20:00) — under EST (~Nov-Mar) the real session is 14:30-21:00
   UTC, so the fixed window silently dropped real closing-half-hour
   trades from the breakdown entirely (`frac > 1`) and shifted the rest
   by nearly a full third-of-session bucket. Fixed with stdlib
   `zoneinfo` (`America/New_York`, real DST-aware local time — no new
   dependency, Python 3.12 stdlib); NSE is unchanged (fixed IST offset,
   genuinely no DST to account for). Two regression tests added, one per
   DST side of the year.
9. Minor, all fixed: `win_rate`'s denominator counted closed-but-`NULL`-
   `net_pnl` trades while `expectancy`/`avg_win`/`avg_loss` silently
   didn't (now consistent); the `"30d+"` expiry bucket label actually
   meant `dte >= 31` (relabeled `"31d+"`); `expectancy` left Decimal's
   ~28-digit division noise in `performance.json` (now quantized to
   money precision, `numeric(18,4)`'s own convention); `manifest.
   strategy_versions` listed every `Strategy` row for the segment rather
   than ones that actually signalled in the window (now scoped via
   `Signal.strategy_id`); `avg_holding_secs` could return an `int`
   despite its `float | None` annotation (now always `float`).
10. Documented rather than changed (architecturally defensible choices,
    not bugs): `trades.jsonl` is scoped by `opened_at` while `equity.csv`
    is scoped by snapshot `ts`, so a trade spanning the window boundary
    can appear in one and not the other; `context_entry.underlying_px`
    (1-minute bar close, what the engine actually acted on) and
    `context_exit.underlying_px` (the exit quote's own vendor spot) come
    from deliberately different sources; `equity_rollup_daily`'s
    `max_drawdown_pct`/`return_pct` are whole-account/period-boundary
    figures, not reset-per-day. All spelled out in the bundle's own
    `README.md` rather than silently assumed.

All 10 fixes re-verified live on the VM after redeploying: all 4 engine
services restarted clean (no errors in logs), a fresh export shows
`data_quality.json` now correctly differing between segments, `--to`
inclusive confirmed (`window_to` in the manifest is one day past the
given `--to`, and the 2 real trades stayed present). 380 tests passing
(was 370), ruff/mypy clean, locally and on the VM.

### 11f. Known gaps, not fixed this session (deliberate, not overlooked)

- **The exit criterion's "one month of data, findings imported back"**
  hasn't happened for real yet — see §11d. The mechanism is built and
  live-verified against every kind of data that currently exists;
  revisit once real calendar time has passed and at least one trade has
  closed.
- **`chain_snapshots_total`/`incomplete` scoping by watchlist membership
  is an approximation**, not point-in-time-exact the way
  `watchlist_membership` itself is (a symbol that changed segments or was
  added/removed from the watchlist is scoped by its full membership
  history). Documented in the bundle's README; revisit if this ever needs
  to be exact.
- **`ChainSnapshot.complete` is `False` for every row ever recorded**
  since P0/P1 (`expected_count` is never populated by any vendor client)
  — not a P5 bug, a pre-existing characteristic P5's `data_quality.json`
  is simply the first thing to ever report on numerically. Documented in
  the bundle's README; nothing downstream depends on this column being
  meaningful today.

---

## 12. P6 — API & dashboards (2026-08-06)

Scope per ARCHITECTURE.md §15/§16/§19: "FastAPI read layer, WS stream,
Next.js master + parameterized segment dashboard, charts." Exit
criterion is "all five dashboards live; p95 < 200ms on overview
endpoints" — the five dashboards are live (§12c); p95 latency wasn't
formally load-tested (single-user tool, no load to test against yet),
but every endpoint hit during live verification returned in well under
200ms against real VM data (§12c).

### 12a. `kairodex/api/` — the FastAPI read layer (7 tests, + extensive live verification)

Every endpoint ARCHITECTURE.md §15 names: `health`/`health/feeds`,
`segments` (list/overview/positions/opportunities/trades/trades-detail/
signals/performance/equity-curve/analytics-breakdown/risk),
`master/overview` (multi-segment, `?ccy=` conversion via `fx_rates`),
`instruments/{id}/chain`, `strategies` (list/report/promote),
`research/notes`, `segments/{seg}/breaker` + `/kill` (both audited via a
new `audit_log` table), `exports` (in-process — `kairodex.export` is
allowed) and `backtests` (shells out to the CLI — `kairodex.backtest` is
forbidden to the API by the import-linter contract "API is glue, not
business logic," and the `import-linter` fix in §12d.4 is what finally
let that contract actually run and confirm it).

**Two previously-inert safety controls became real.** Before this
session, `risk.loader.build_account_state`'s `kill_switch_engaged`
parameter only ever took its hardcoded `False` default — nothing
anywhere could ever set it `True` for a real reason, so the kill switch
named first in the gate chain's own docstring order (`kill switch ->
breaker state -> ...`) was structurally inert in the live engine since
P3. It now reads a new `system_state` singleton table by default.
Similarly, `risk.accounting.update_equity_and_risk_state` recomputes
`breaker_status` from scratch every engine tick (~60s) — without a fix,
a manual trip via the new `/api/segments/{seg}/breaker` endpoint would
have silently un-tripped itself before a human could act on it.
Accounting now respects a sticky `MANUAL_`-prefixed `breaker_reason`
convention, only cleared by an explicit re-arm through the same
endpoint — matching SPEC_REVIEW.md B8's "both persisted, both requiring
explicit human re-arm."

**Live-verified by actually engaging both**, not just unit-tested: `POST
/api/kill {"action":"engage",...}`, waited a full tick cycle, confirmed
via `psql` that **20 real signals were rejected at `reject_stage=
"kill_switch"`** in that window — then released, confirmed normal
evaluation resumed. `POST /api/segments/nse_index/breaker
{"action":"trip",...}`, waited a full tick cycle, confirmed the segment
stayed `TRIPPED`/`MANUAL_HALT: ...` (not silently reverted to `ARMED`),
then re-armed. Every action produced a real `audit_log` row with
accurate `before`/`after` state, verified via direct SQL.

`POST /api/exports` and `POST /api/backtests` also live-verified against
real data: a real bundle (2 trades, real sha256 hashes) and a real
`kairodex backtest run` (7,525 signals, matching P4's own numbers)
round-tripped through the API exactly as the CLI does directly.

### 12b. `kairodex/streaming/` + WS fanout (the first real Redis consumer since P1)

A small, neutral package (`types.py`'s `StreamMessage` discriminated
union — `tick`/`signal`/`position_update`/`trade_closed`/`risk_update`/
`feed_health`, `bus.py`'s thin publish wrapper) living where neither the
publisher (`kairodex.engine`) nor the subscriber (`kairodex.api`) owns
it — `kairodex.api` may not import `kairodex.engine` at all under the
import-linter contract. `live_loop.py` now publishes `signal`/
`trade_closed`/`position_update`/`risk_update` on its existing 60s tick
cadence; `tick` and `feed_health` are declared in the union but have no
publisher yet (no current consumer needs raw market ticks over this
channel; feed status is already served by the fast-changing-rarely
`/api/health/feeds` REST endpoint). `GET /ws/stream?segments=...`
subscribes to one Redis channel and filters client-side.

**Live-verified two ways**: subscribed directly to the Redis channel
(`redis-cli subscribe kairodex:stream`) during real market evaluation
and captured real signal-rejection/risk-update/position-update messages,
including a real growing-profit `position_update` for the same
BANKNIFTY position §11a found; then connected an actual WebSocket client
to `/ws/stream?segments=nse_index` and confirmed it received only
`nse_index` messages, correctly filtered.

### 12c. Frontend — Next.js 16, app router + TypeScript + Tailwind + Lightweight Charts

Two routes — `/` (master: per-segment equity, feed health, strategies,
research insights, live activity) and `/segment/[segment]` (equity
curve chart, open positions with live Greeks, opportunities, trade
history with drill-down to `/segment/[segment]/trades/[tradeId]`'s full
event timeline, risk panel, live activity) — matching the spec's "four
dedicated segment dashboards... same detail" via one parameterized
component, not four copies (ARCHITECTURE.md §16's own instruction).
That's the five dashboards the exit criterion names (master + 4 segment
instances of the one route).

Palette and status colors are the `dataviz` skill's validated reference
instance (`references/palette.md`), unmodified — six-checks validator
run, both light and dark modes pass. `shadcn/ui` itself wasn't installed
(no interactive terminal to run its init CLI against, and a single-user
internal tool doesn't need its component surface) — a handful of small,
hand-rolled Tailwind primitives (`Card`, `Badge`, `StatTile`) cover
everything used, a deliberate, documented deviation from the doc's
literal stack, same class of decision as P2's Bjerksund-Stensland swap.

**Every page is `force-dynamic`, not statically generated with ISR** —
see §12d.6 for why the first deploy attempt got this wrong. Every fetch
is `cache: "no-store"`. This is a live trading dashboard on a 60s tick
cadence for a single user where a fresh per-request fetch (a localhost
hop) costs nothing; the correctness of "never show data older than the
engine's actual last tick" was worth more than the marginal performance
of static generation.

**How to reach the dashboards**: both `kairodex-api` (127.0.0.1:8000)
and `kairodex-frontend` (127.0.0.1:3000) bind localhost-only on the VM —
`kairodex.api.main`'s own docstring explains why (this process holds
`POST /api/kill`). Reach them from a laptop via SSH tunnel, the same
access pattern already used for everything else in this repo, not a new
firewall rule: `ssh -L 8000:localhost:8000 -L 3000:localhost:3000 -i
~/.ssh/id_ed25519_personal root@164.52.206.92`, then open
`http://localhost:3000` in a browser. `frontend/.env.production`'s
`NEXT_PUBLIC_API_BASE_URL=http://localhost:8000` is what makes the same
URL work for both the Next.js server (running on the VM, "localhost"
means the VM) and the browser (tunneled to the same port number) — see
`frontend/src/lib/api.ts`'s own docstring.

**Live-verified with real data end to end**: `curl` against the deployed
`kairodex-frontend` renders the real `BANKNIFTY 58400.0 C 2026-08-25`
open position (with live Greeks and a real, growing unrealized P&L) on
the segment dashboard, and the trade-detail drill-down renders that
trade's real `FILLED` event from `trade_events`. All 9 systemd units
(2 ingest, 1 jobs, 4 engine, `kairodex-api`, `kairodex-frontend`)
confirmed `active (running)` with clean logs after every restart this
session.

### 12d. Subagent review pass — 9 findings, all fixed (388 tests, 24 new this phase)

Per the same discipline as P3/P4/P5, an independent subagent reviewed
the full P6 diff — re-derived the sticky-manual-breaker logic by hand
(including building a scratch SQLAlchemy reproduction to check exactly
what gets written to the DB under a stale read), inspected real orphaned
connections on the VM, probed the export path-traversal guard with real
encoded-payload requests, and swept the entire frontend for every place
a `Decimal`-typed field could still bypass the coercing formatters. It
found 9 real issues (2 CONFIRMED-serious, the rest lower-severity or
narrow-window), all fixed here:

1. **The WS handler leaked one Redis pub/sub connection + one live task
   per client disconnect it never noticed.** `stream()` only ever
   learned a client was gone when a `send_text` on a *matching* message
   happened to fail — a client with a narrow `?segments=` filter, or a
   half-open TCP connection (a dropped SSH tunnel, a sleeping laptop),
   could go unnoticed indefinitely. **Confirmed live**: a subscriber from
   the original verification session was still connected 25+ minutes
   after its tunnel closed, with zero `connection closed` log lines the
   whole time. Fixed: race `websocket.receive_text()` (to detect a
   disconnect promptly via ASGI's own signal) against the pubsub forward
   loop with `asyncio.wait(..., return_when=FIRST_COMPLETED)`; either
   side finishing tears the whole pair down.
2. **`/segments/{seg}/trades`'s "R" column could never show a value.**
   `TradeRecord.r_multiple` is a `@property`, not a dataclass field —
   FastAPI's `jsonable_encoder` serializes dataclasses by field only, so
   the key was silently absent from every response, forever, including
   for closed trades. Fixed: route through `kairodex.export.bundle`'s
   `trade_export` (promoted from a private helper to a shared one — it
   already existed for exactly this "TradeRecord -> real JSON shape"
   job, reused rather than reinvented) instead of relying on automatic
   dataclass serialization. Also added the same field to the trade-detail
   endpoint's hand-built dict (computed inline, same formula).
3. **Two unguarded `datetime.fromisoformat()` calls turned a malformed
   query param into an HTTP 500 with a raw ASGI traceback**
   (`/segments/{seg}/trades?from=garbage`, `/instruments/{id}/chain?
   at=garbage`) instead of the clean 422 `deps.parse_window` already
   modeled. Fixed with a new shared `deps.parse_iso_date` both call sites
   now use.
4. **`docs/PROGRESS.md` had no P6 section at all** before this pass —
   the "read this file first" instruction had nothing to read about the
   API/frontend deployment, the new `kairodex api` CLI command, the new
   systemd units/ports, or the two live-caught bugs, all of which existed
   only in git history. This section is that fix.
5. **`/api/backtests`'s subprocess-correlation could return the wrong
   run.** After the CLI subprocess exits, the handler re-queried "newest
   `backtest_runs` row for this segment/strategy" — but `created_at` is
   stamped at a run's *start*, not its write time, so a slower run
   started earlier could finish (and be shadowed by) a faster run started
   later, silently returning someone else's result. Fixed: parse the real
   `run_id` the CLI already prints on its own last line
   (`"backtest_runs row {id} written"`) instead of re-querying.
6. **A narrow (~10% of wall-clock) race could still drop the sticky
   manual-breaker sentinel.** `update_equity_and_risk_state` reads
   `RiskState` once near the top of each ~60s tick; SQLAlchemy's
   `expire_on_commit=False` means that read can be stale for the
   remainder of the tick's active ~6.5s phase. If a manual trip lands via
   a different session in that exact window *and* an auto-trip condition
   is independently true, the auto-trip write wins — `breaker_status`
   stays safely `TRIPPED`, but the `MANUAL_` sentinel is overwritten,
   silently making the halt auto-un-trippable the next calendar day
   instead of requiring the intended explicit re-arm. Fixed:
   `session.refresh()` the row immediately before the sticky check,
   narrowing the race window from "the whole tick" to "one query."
7. **A frontend trade-detail page's local TypeScript interface had
   drifted back to `number` for Decimal-sourced fields**, undermining
   the compile-time safety `Decimal = string` (§12d.9 below) exists to
   provide. Fixed to use the shared alias; also added the now-available
   `r_multiple` field to its display.
8. **`feed_health` messages (`segment: null`) could never reach a
   filtered WS client** — no real segment value can ever string-match a
   literal `null`, so `?segments=nse_stock` would silently drop a
   provider-scoped message forever. Dormant today (nothing publishes
   `feed_health` yet, §12b), fixed for when something does: a `null`
   segment now always forwards regardless of a client's filter.
9. **Minor, all fixed**: `streaming.bus.publish`'s failure log included a
   full traceback per call — with Redis down that's up to 4/tick across
   4 segments, indefinitely, flooding the journal for an already-explained,
   non-fatal condition (now a one-line warning); `RecentActivity.tsx`
   rendered a live position's raw Decimal-string mark instead of through
   `fmtNum`; `parse_window`'s docstring said "the epoch" for a fixed
   2020-01-01 anchor.

**Not fixed, explicitly low-severity** (subagent's own assessment,
independently checked and agreed with): a TOCTOU gap on the audited
writes (`control.py`/`strategies.py` read-then-write with no row lock) —
real, but this is a single-user tool driven by a human clicking one
button at a time, not worth the complexity of optimistic locking for a
race that needs two simultaneous requests from the same one user.

**Cross-checked and confirmed correct, not just claimed**: `kairodex/
strategy/__init__.py`'s absence really did crash `lint-imports` outright
(reproduced live in a scratch worktree at the pre-P6 commit); `pyproject.
toml`'s missing `include_external_packages = true` really was a second,
independent blocker for the same command; the kill-switch default really
does take effect within one tick with no restart; the export
path-traversal guard really is sufficient against every encoding trick
tried (`..%2f`, double-encoding, embedded nulls, `--path-as-is`); the
`useStream.ts` reconnect logic has no client-side leak (the leak was
entirely server-side, finding #1); every other Decimal-vs-number spot in
the frontend was already safe.

388 tests passing (381 + 7 new this phase — `tests/unit/api/`, DB-free:
app-assembly/route-inventory and `parse_window`), ruff/mypy/`lint-imports`
clean; `next build`/`eslint`/`tsc --noEmit` clean. `tests/unit/
test_lse_expiry_probe.py`'s one failure is the same pre-existing,
unrelated calendar-drift issue flagged since the P5 review pass (§11e) —
confirmed still present at the pre-P6 commit too, not a regression.

### 12e. Known gaps, not fixed this session (deliberate, not overlooked)

- **`fx_rates` has zero rows** — `?ccy=` on `/api/master/overview`
  correctly degrades to `null` for non-native currencies rather than
  fabricating a rate, but nothing populates the table yet and the
  frontend doesn't expose a currency selector (would be dead UI against
  data that doesn't exist). Revisit together once a real FX-rate sync
  exists.
- **Master dashboard doesn't show a single combined cross-segment equity
  figure** (blocked on the same `fx_rates` gap — INR and USD segments
  can't be summed without a real conversion rate) — the four per-segment
  tiles are shown instead.
- **`tick` and `feed_health` stream message types have no publisher** —
  declared in the union, consumed correctly by the WS filter (§12d.8),
  but nothing calls `publish()` for either yet. Revisit if a live price
  chart or a real-time system-health panel is ever built against them.
- **p95 latency wasn't formally load-tested** — every endpoint measured
  during live verification was well under 200ms, but that's one user, one
  request at a time, not a load test. No load-testing infrastructure
  exists in this repo yet; revisit if/when this ever needs to serve more
  than one concurrent user.

### 12f. Public read-only mirror — `app.swingpro.tech` via Surge (2026-08-06)

The user's own explicit request, after P6 itself was done: a way to
check on progress/performance from a browser without an SSH tunnel.
Surge (surge.sh) only serves static files — no Node process — so the
live dynamic dashboard (§12c, `force-dynamic`-equivalent per-request
rendering) can't run there directly. Rather than exposing `kairodex-api`
to the public internet to let a remote browser fetch live (which would
also expose `POST /api/kill` and the other audited control endpoints —
a real security question this repo's own posture, SPEC_REVIEW.md B5,
already answers as "no"), the frontend gained a second build mode from
the *same* source tree (`NEXT_OUTPUT_MODE=export`, `next.config.ts`):
a static export, built ON THE VM (where `127.0.0.1:8000` is the real
API), baking real data into static HTML at build time — the "how
`kairodex-app`/`app_role_separation` finally made a REVOKE mean
something" of frontend deployments, same idea: least exposure that still
does the job.

`generateStaticParams` came back for both dynamic routes for this mode
only (`[segment]`: the fixed 4 values; `[tradeId]`: every real trade's
own id, fetched from the live API at build time, with a one-route
fallback so the build doesn't hard-fail before any trade has ever been
taken). The live server build (`kairodex-frontend`, unchanged) still
renders every request fresh exactly as §12d's fix established — the two
modes share `lib/api.ts`'s single `apiGet`, which switches its own
`cache` option by the same env var, not two copies of the fetch logic.

Deployed: `frontend/deploy-surge.sh` (build + `surge ./out
app.swingpro.tech`), run once manually (confirmed live: real HTTPS via
Surge's own cert, the real `BANKNIFTY 58400.0 C 2026-08-25` position
render, the trade-detail drill-down resolves) and then wired to a new
`kairodex-surge-deploy.timer` (systemd, `OnUnitActiveSec=5min`) so the
public snapshot keeps refreshing without a person remembering to run it.

**Known, deliberate limitation of this mirror specifically** (not the
live VM+tunnel dashboard, which is unaffected): the "live activity" WS
panel (§12b) cannot connect from `app.swingpro.tech` — the browser has
no path to `kairodex-api` at all from there, by design — so it renders
its own empty state permanently on this mirror. Every other panel is
real data, just as fresh as the last 5-minute rebuild rather than
per-request.

**Incident, 2026-08-07: silently frozen for ~10 hours.** User-reported
stale open-position marks that survived a hard refresh, cleared
cookies, and a different browser — all pointless against this, since
the page itself hadn't changed at the source. Root cause: Surge's
free-tier plan rate-limits how often one domain can be republished,
and `OnUnitActiveSec=5min` blew through it. Every redeploy from
02:25 IST onward aborted with `Rate limited. (domain). Verify email or
try again in 15 hours` (101 failures counted that day) — the timer
kept firing and kept failing, quietly, with nothing surfacing it
anywhere; the public site stayed pinned to the 02:20 IST snapshot
(pre-market) the whole time. Fixed in two parts: the user verified
their Surge account email, which lifted the block immediately (a
manual `systemctl start kairodex-surge-deploy.service` succeeded on
the first retry, confirmed via `curl -D-` showing `surge-cache: MISS`
/ `age: 0` and the freshly-baked mark); and `OnUnitActiveSec` widened
5min -> 20min (VM-local unit file, not checked into the repo — same as
`.env`, see §2) so the same cadence doesn't re-trigger the limit.
**Lesson: an automation loop that fails needs its failure to be loud
somewhere** — a `Rate limited` abort every 5 minutes for 10 straight
hours produced zero signal outside `journalctl` on a box nobody was
tailing; `kairodex status`/dashboard health has no notion of "is the
public mirror itself current," only of the live VM dashboard's own
data freshness, which was never actually affected here — worth adding
a real check (e.g. a `Surge-Cache`/`age` header probe, or the
deploy script exiting loud enough for a monitoring hook) if this class
of gap needs closing.

---

## 13. Post-P6 findings — what three days of live running exposed (2026-08-06)

Not a phase. P6 finished, the stack ran unattended, and answering two
plain user questions ("show entry time and SL/TP", "when does it trade",
"why has US never traded") turned up four live faults that no test could
have caught, because each was a *disagreement between the code and the
world* rather than a broken invariant. Recorded here because the pattern
matters more than the individual bugs: **everything below was silently
wrong while every dashboard read green.**

### 13a. Entry time (IST) + SL/TP on positions/trades

User request. `TradeRecord`/`TradeExport` gained `profit_target`
(mirroring the existing `initial_stop_price`, both from
`Trade.risk_params`), so it flows analytics.loader -> export.bundle ->
API like everything else. `/segments/{seg}/positions` and
`.../trades/{id}` also expose the *current* (possibly-ratcheted)
`stop_price`, distinct from the entry-time `initial_stop_price` that
analytics keeps for R-multiple math. Frontend: new `fmtTsIST` — always
Asia/Kolkata regardless of the viewer's browser timezone or the trade's
market, since it is one user's own reference timezone.

### 13b. The live trading gate was DST-naive (found while answering "when does it trade")

`risk.loader.is_session_open` — the gate deciding whether the engine
evaluates signals at all — carried a hardcoded `13:30-20:00 UTC` US
window with its EST/EDT comment backwards. A P6 subagent review had
already fixed the *other* copy of this approximation
(`analytics.breakdowns`) to be DST-aware; the fix never propagated to the
one that matters for trading. In August (EDT) it was correct by
coincidence; every EST month (Nov-Mar) it would have been an hour wrong
with nothing to alert on. Both call sites now share
**`kairodex/core/sessions.py`** (`session_window_utc`,
`is_session_open_now`, `local_date_for`), so a future DST fix cannot land
in one and miss the other. The recorder's session gate (§13d) uses it too
— recorder and engine can no longer disagree about "open".

Real answer: **NSE 09:15-15:30 IST; US 09:30-16:00 ET (DST-aware)**.
Still no holiday/half-day calendar (ARCHITECTURE.md §6, pre-existing).

### 13c. US has never traded, and it is not a strategy problem

15,084 US signals, **100% rejected** at `NO_CANDIDATES_IN_EXPIRY_WINDOW`
— every one, across every hour, whether that hour saw 375,000 fresh
quote rows or zero. Not one reached the risk gates.

Cause: **LSE publishes no bid/ask for options at all.** Confirmed live
against the streaming feed during an open US session — 400 consecutive
option ticks, every one `bid=None, ask=None`. The vendor's `Tick` type
declares the fields; they are never populated. These are trade prints
(time & sales), not quotes; the REST `options()` chain has no bid/ask
columns either. What we *do* get is real and live: last price, volume,
IV, full greeks, underlying price.

`orchestrator.py`'s `_candidates_from_chain` skips any quote with
`bid is None or ask is None`, so the candidate list is always empty
before the selector ever runs; `ContractCandidate.bid/.ask` are
non-optional, and `execution/fills.py` prices every fill off
`mid +/- half_spread`. Both were written against Upstox, which does
deliver depth. **Not yet fixed** — see §13e.

Asymmetry worth knowing: the *exit* path already degrades gracefully
(`bid=latest_quote.bid or mark`). Only entry hard-skips.

### 13d. LSE quota exhausted — 28,152 rejected calls in 36h

US ingestion had been dead since 04:05 UTC with the daily cap (15000/day)
gone and the rolling *weekly* cap also tripped. Nobody had multiplied:
22 US underlyings x 3 calls x 1440 min = **~96,000 calls/day** against a
15,000 cap — exhausted in 3.7h, every day, by design.

Fixed (`recorder.py`, `lse/client.py`):
- **Session gate** — the poll ran 24/7; US options trade 32.5 of every
  168 hours, so ~81% of the quota went to a closed book.
- **Per-day expiry cache** — `list_expiries` refetched a whole chain
  every 60s to learn dates that change weekly (a third of the budget).
- **Real 429 backoff** — `RateLimitError` already existed, unused, with
  the docstring "callers should back off, not retry immediately". LSE
  now raises it; a quota rejection aborts the whole cycle (an
  account-level fact, not a per-underlying one) and sleeps 30 min.
  `_probe_expiries` re-raises instead of swallowing it and burning 14
  more calls; startup bar backfill likewise.
- **Interval** — gate + cache alone still came to 17,572/day, so
  `T1_POLL_INTERVAL_BY_MARKET` puts US at 120s (8,797/day, ~59% of cap).
  NSE stays 60s; tuning the shared loop to the tolerant vendor is what
  caused this. **US chain snapshots are therefore 2 min apart** — a real
  vendor-imposed resolution limit, worth knowing before reading US
  features. A test recomputes the budget rather than asserting a magic
  number, so watchlist growth trips CI, not the vendor.

**The sensor was inverted**, which is why it ran three days unnoticed:
`/usage` is itself metered, so it 429s once you are over — and `quota()`
caught that and returned `used_pct=0.0`. `feed_health` read a reassuring
**0.00% while the account sat at 100%**. A 429 now reports 100.0;
any other failure reports `None` (unknown). `QuotaStatus.used_pct` is
`float | None` — the DB column and `kairodex status` already handled
nullable; only the dataclass type was lying.

Verified live: 7,920 429s in a comparable pre-fix window -> **0** after,
`feed_health.lse.quota_used_pct` now reads 100.00 with the real error.
The weekly cap was already tripped, so US ingestion stays down until it
clears on its rolling window — the fix stops the bleeding, it does not
restore service instantly.

### 13e. US last-price entry path — done (2026-08-06)

`kairodex/execution/synthetic_quote.py` models a book from last price for
US only, gated by an explicit `segment.market is Market.US` opt-in rather
than "synthesize whenever bid/ask are missing" — the latter would let an
NSE feed hiccup silently move a real-book segment onto modelled prices,
which is worse than the bug being fixed. Three rules, each pinned by a
test: recorded data is never touched (`option_quotes.bid/ask` stay NULL
and faithfully so); it errs expensive (4% of premium, well above real
liquid US spreads, below fills.py's own 500bps limit) because a too-tight
spread manufactures edge that does not exist; and every such fill is
labelled on the trade (`context_entry.synthetic_quote`).

`SPREAD_PCT` is an assumption, not a measurement — there is no bid/ask
from this vendor to calibrate against. Not YAML config yet, deliberately:
a knob implies a calibration that does not exist.

**The size proxy mattered more than the spread.** Measured on real legs,
US volume is far thinner than intuition suggests (p99 ~102/day; the
busiest contract in the entire watchlist did 3,628). A first-pass
volume/100 proxy left only 1.4% of legs at size >= 1, and fills.py fills
`floor(0.25 * top_of_book)` — so size 1 fills zero lots. That would have
swapped `NO_CANDIDATES_IN_EXPIRY_WINDOW` for
`NO_LIQUIDITY_AT_TOP_OF_BOOK` and left US still not trading, one line
further on. The proxy now scales sub-linearly (volume/20, capped at 50),
and below the size that can fill one lot `synthesize_quote` returns None
— the contract fails to be a *candidate* rather than being selected on
delta and rejected at execution every time.

Verified by replaying 21,734 real recorded US legs through the real
selector and the real fill model: **0 candidates before, a fillable
contract on 11 of 16 underlyings after.** The 4 underlyings with nothing
liquid enough (COST, DIS, WMT, XOM) are correctly excluded rather than
traded on fiction, and BAC's pick was rejected `SPREAD_TOO_WIDE` by the
fill model's own policy. Replay used recorded data, not a live session —
US ingestion is still down until the LSE weekly cap clears (§13d), so no
live US trade has been taken yet.

Also 2026-08-06 (user's call): `nse_stock` `max_concurrent` 1 -> 5.
ARCHITECTURE.md §11's table now states configured `max_concurrent` rather
than the old "realistic concurrency" prediction. The capital math still
sits below the new ceiling — `exposure_cap_pct` 0.40 caps exposure at
Rs 20,000 while `max_premium_pct` 0.35 allows Rs 17,500 in one position,
so the exposure gate, not `max_concurrent`, remains binding.

### 13f. Open, in priority order

1. **Zero closed trades, ever.** 3 open, 0 closed. Realized P&L, fees,
   R-multiple and equity update have run in tests only, never in
   production. Every P7 gate is a closed-trade statistic. The 3-day theta
   guard means the first natural close is ~2026-08-09.
2. **US ingestion still down** until LSE's rolling weekly cap clears
   (§13d). The entry path is fixed and replay-verified, but no live US
   trade can happen until data flows again.
3. **Re-check the synthetic spread and size proxy** once any US trade
   closes — they are the two least-evidenced numbers in the live path.
4. Then P7 (strategy build-out).

**Standing constraint on all of the above** (user, 2026-08-06): trade
only high-conviction setups — neither overtrade nor undertrade. A low
take-rate is not itself a bug. But a rejection for *plumbing* (§13c) is a
bug wearing a conviction rejection's clothes, and the two must never be
conflated when tuning gates.

### 13g. The "live" dashboard had not been live (2026-08-06)

Found while doing a routine "update the VM and frontend": the dashboard
rendered a stop of `376.04` and a Target of `—` while `/api/segments/
nse_index/positions`, queried at the same instant, returned `384.65` and
`922.740000`. Two independent bugs, both introduced by §12f's own mirror:

1. **The export build clobbered the live build.** Both modes wrote the
   default `.next`, and `deploy-surge.sh` runs in the same checkout, on
   the same VM, as `kairodex-frontend` — whose unit is a bare
   `npx next start` with no build step. So every Surge deploy replaced the
   live server's build with a static export, and the 5-minute timer did it
   again every 5 minutes. `next.config.ts` already *said* the export must
   be "a *separate* build mode ... not a replacement for it"; sharing
   `distDir` made it exactly a replacement. Export now builds into
   `.next-export`.
2. **The mirror was stale too.** The export fetches at build time with
   Next's default caching (a static export has no server, so `no-store`
   isn't available to it), and Next persists those results in
   `.next/cache/fetch-cache` **across builds** — so the timer rebuilt
   faithfully and re-baked the same payloads forever. Demonstrated rather
   than assumed: the 20:06:57 export baked `376.04`/null-target while the
   API already served `384.65`/`922.74`; once the cache was cleared, the
   next export baked the correct values. `deploy-surge.sh` now drops the
   fetch cache before each build.

**Non-obvious detail that caused a third, self-inflicted bug:** with
`output: "export"`, `distDir` **is** the directory the exported site is
written to — it does not merely relocate build artifacts and leave `out/`
as the export target. After splitting `distDir`, `deploy-surge.sh` kept
uploading a stale leftover `out/`, and `surge` reported `Success!` each
time because the path still existed. (It aborts loudly when the directory
is genuinely absent — that is how this was caught.) It now publishes
`.next-export`, verified to contain only the site: `404.html`,
`index.html`, `_next/`, `segment/`, `CNAME`, no build internals.

Verified after the fix: live dashboard and mirror both render
`384.65 / 922.74`, the live routes build as `ƒ (Dynamic) server-rendered
on demand` and answer with `Cache-Control: private, no-cache, no-store,
must-revalidate` (a static export cannot), the live build survives an
export deploy unchanged, and the timer's own service unit — not just a
manual run — completes with `Result=success`.

**Worth generalising:** every fault in §13 was silent. This one had a
green systemd unit, a green `Success!` from Surge, and a dashboard that
rendered plausible numbers. The only thing that caught it was comparing
two sources of the same fact at the same instant.

---

## 14. The first eight real trades, audited (2026-08-07)

Prompted by a plain question — "analyse the ongoing, rejected and closed
trades" — with eight real trades and ~33k signals finally on disk. Six
faults, every one of them silent, none reachable by the 438 tests that
were passing at the time. Fixed in `3e627ca` + `5fa1d69`; **447 tests
now**, each new one written against the real recorded numbers of the
trade it came from and checked to fail against the old code.

### 14a. Stop-losses were not executing, and nothing said so

The headline. Trade 2 (HDFCBANK 750 C) marked **8.75 then 8.70 against an
8.75 stop** on two consecutive ticks on 7 Aug and stayed open. It survived
only because the price recovered on its own.

Chain, verified end to end: `compute_fill` rejects any quote older than
**2s**; the engine reads `option_quotes` rows written by the **60s** T1
REST poll, not a tick stream; `now` was captured once per cycle and the
cycle takes tens of seconds to walk the watchlist. Measured over **1,396
real engine ticks, only 1.9% carried a quote fresh enough to fill.** Both
of trade 2's stop attempts were rejected `STALE_QUOTE` — and
`run_exit_tick` returned `..._FILL_FAILED` with no log line and no DB
row, so nothing recorded that a stop had failed.

The one exit that did fill (trade 6's R1 partial) filled *by accident*:
its quote was stamped 3.2s **after** the cycle's stale `now`, giving a
negative age that slipped past the `age > 2000ms` check.

Fixed three ways: exits log a warning and append an **`EXIT_FAILED`**
event (reject reason + quote age); entries and exits get separate
staleness allowances (**90s / 300s** — skipping an entry is free,
skipping an exit means carrying a position past its own stop); and `now`
is re-read per trade so the age measured is real.

### 14b. The trailing-stop ratchet blocked all profit-taking

`trailing_stop_check` returns a `qty_lots=0` bookkeeping decision and
`evaluate_exits` was "first match wins" — so **on any tick that made a
new high, no profit target or R-target could fire.** That is precisely
when they get crossed.

It cost the only closed trade outright. Trade 4 (Nifty 24750 C): 1R =
116.17, peak **117.35** — it crossed 1R on exactly one tick, that tick
also made a new high, the ratchet matched first and returned. Next tick
113.45. It later stopped out at **-₹271 having been +31%**. Trade 6 hit
1R at 06:00:47 and only partial-exited at 06:11:06, ten ticks later, once
it stopped making new highs.

The ratchet is now held back and returned only if nothing else fires.
It is dropped rather than merged onto the winning exit: `hwm_price` is
persisted on every path, so the next tick re-derives and logs the move.

### 14c. No conviction floor — first-come-first-served slot filling

`nse_stock` opened **4 of its 5 slots in a single tick** at the open (one
at confidence **0.1366**), then rejected **12,696** later signals on
`MAX_CONCURRENT`, some scoring as high as **0.7344**. A slot is scarce:
taking a weak setup locks out every better one until it closes — the
exact opposite of the stated "only high-conviction setups" philosophy.

Added `min_confidence` per segment, set near each segment's **own**
p75–p90 because the distributions differ ~10x (nse_stock 0.35, nse_index
0.30, us_stock 0.25, us_index 0.20). Sanity-checked against all history:
21.3% / 17.8% / 8.4% / 0.5% of signals would clear. Below-floor signals
are still written as rejections — unlike an out-of-hours one, a weak
score is real training data. **Not fitted, not backtested** — same status
as `stop_loss_pct` and the R-targets, recalibrate in P4.

> `us_index` at 0.5% (13 signals ever) is effectively "do not trade this
> segment". Left deliberately: its max observed confidence is 0.2317 and
> its p95 is 0.0239, so any floor that lets it trade is a floor that
> trades noise. The real fix is upstream signal quality, not the knob.

### 14d. NSE costs were 25x too small

`premium` handed to the cost model was `price * lots`, omitting the
contract multiplier, so every premium-based NSE charge (STT, exchange
txn, SEBI, stamp duty, and the GST computed on them) was levied on
1/`lot_size` of the real premium. Trade 2 recorded **₹0.43 of fees on a
₹13,341 premium** against a true ₹10.65. `OrderRequest.lot_size` is now
**required, not defaulted to 1**, so no future call site can silently
reintroduce it.

### 14e. Upstox WS: a refusal retried like a blip — NOT fixed, needs a token

401/403 on the *handshake* is not transient, and it was being retried
every 60s forever — **327 times over five hours** on 7 Aug — while the
same token kept serving REST market data with HTTP 200. Auth-class
rejections now back off to a **15 min** cap (verified live: 64s → 128s,
past the old 60s ceiling).

**The feed itself is still down and this is not a code fix.** Diagnosed:
`/v2/feed/.../authorize` is **410 Gone**; `/v3/.../authorize` returns
**200** and issues a URL; connecting to that fresh URL still gets **403**
(and **401** if the Bearer token is attached), with the recorder stopped
so nothing was competing for a connection. REST market data with the
identical token returns 200. So the token is valid for REST and refused
for the feed — **an account/token action is needed, not a code change.**
Cost of the outage: quote freshness drops from sub-second to 60s, which
is what made §14a's 2s threshold catastrophic rather than merely wrong.

**Re-diagnosed 12 Aug (still down, day 6).** Every remaining
implementation-side cause Upstox support names for this 403 is now ruled
out by direct test, so the ball is definitively not in our court:

| Hypothesis | Test | Result |
|---|---|---|
| Single-use URL reused | fresh `authorize` per attempt | **403** |
| Missing/mismatched handshake headers | bare / `Bearer` / `+Api-Version: 3.0` | **403** all three |
| Wrong connect path | direct `wss://api.upstox.com/v3/feed/market-data-feed` | **403** |
| VM's IP blocked | same handshake from a second, unrelated IP | **403** |
| IPv6 egress vs. IPv4 whitelist | forced `-4` | **403** |
| Token expired | valid to **2027-05-02**; REST 200 | not it |
| Concurrent-connection limit | fresh probe between retry windows | **403** |

A **bogus** `code`, and the feeder URL with **no query params at all**,
both return the *identical* empty-bodied 403 as our real code. That was
first read here as proof the feeder never evaluates our code at all —
**it isn't.** Unknown paths on `wsfeeder-api.upstox.com` return **404**
and only the feeds path returns 403, so the edge is routing and
authenticating normally; a uniform opaque 403 for both a bogus
credential and a valid-but-unentitled one is ordinary auth-gate
behaviour, not an outage signature.

**Resolved 12 Aug 11:50 IST: the token was regenerated, and the 403 did
not move.** That was the one test available that separates "broken on
Upstox's side" from "this token isn't entitled", and it came back
negative. The fresh token serves REST fine (92 consecutive 200s, zero
401s) and gets `authorize` 200 — then 403 at the feeder, exactly as the
old one did. So the refusal is **not token-level**: two independent
tokens, both REST-valid, both refused at the feed, from two IPs.

That leaves an Upstox-side fault or an **account-level** feed entitlement
that regeneration doesn't touch. Both require Upstox; neither is
reachable from this codebase.

The earlier "**401** if the Bearer token is attached" above was an
artifact of that test reusing an already-spent single-use URL; with a
fresh URL, attaching the Bearer gives 403 like everything else.

Two things that look like the cause and are **not**, so they don't get
chased again:
- `/v2/user/profile` returns **UDAPI1221** ("permitted only from the
  static IP configured in your account"). That is *expected* and
  unrelated — Upstox documents account APIs (User, Orders, Portfolio) as
  static-IP-gated for an Analytics Token, while market data **and
  Websocket** are explicitly listed as *not* requiring it.
- Analytics Tokens do support WS — Upstox staff confirmed this on the
  community forum in response to an identical report.

One other user reports the same 403 across three separate IPs starting
**4 Aug**, unanswered by Upstox — suggestive of a server-side cause, but
it's a single forum post and their start date doesn't match ours, so
it's not evidence to lean on.

Token regeneration is now **done and ruled out** (above). **The only
remaining action is an Upstox support ticket**, quoting the ruled-out
table plus the two-token result — every cause reachable from our side
has been eliminated by direct test. Ask specifically whether the v3
market-data-feed entitlement is enabled on the *account*, since that is
the one thing a token regeneration would not have refreshed.

### 14e-bis. The dashboard lied about it for five days — fixed

Through all of the above, `feed_health.upstox` read `last message 2m
ago` beside `1237` subscribed instruments, on a feed whose handshake was
being refused outright. `ws_stream_loop`'s `flush()` stamped
`last_message_at=now()` and `subscribed_count=len(instrument_ids)` on
*every* flush — including the `flush(connected=False)` in its teardown —
so the columns tracked "when did the loop last run" and "how many did we
resolve, ever", not "when did data last arrive". `instrument_ids` never
resets across reconnects, so 1237 was a fossil of the last session that
actually streamed.

This is precisely the false positive §1 gotcha #10 flagged after the
2000-key incident ("prefer deriving liveness from an actual message
counter, not a timer that always fires") — the lesson was written down
but the code was never changed. Now `last_message_at` moves only when a
flush carries real ticks, and `subscribed_count` reports 0 while
disconnected. Fixed in the shared `flush()`, so LSE gets it too.

### 14f. `logging.basicConfig` was never called

Found while checking §14a's new warning actually reaches the journal. It
does — but only because WARNING+ escapes via Python's last-resort stderr
handler. **Every `logger.info` in the codebase went nowhere**, which is
why the four engine units looked silent in `journalctl` for days while
evaluating tens of thousands of signals. Configured in `cli.py`; systemd
already timestamps the journal, so the format carries none.

### 14g. How it was verified

Not just unit tests — both headline failures were **replayed through the
new code against their own real DB rows** on the VM:

- trade 4 at its recorded 117.35 peak → now `PARTIAL_EXIT_R1 qty=1`
  (was `STOP_RATCHET qty=0`).
- trade 2's stop-breach quote → now fills **43 @ 8.72** (was
  `STALE_QUOTE`).
- the real `run_exit_tick` EXIT_FAILED path, run against live trade 6
  inside a savepoint-bound session and rolled back: logged the warning,
  appended the event with reject reason + quote age, hash chain still
  verified, **25 events before and after** — zero pollution.

**Worth generalising, again:** §13's lesson repeated with teeth. Every
fault here was invisible *because the thing that failed had no way to
report failing*. A stop that cannot fill, an exit that returns silently,
an info log with no handler, a retry loop with no ceiling — none of them
error, so none of them page. Prefer a loud failure to a clean return.

---

## 15. P7 begins — hardening first, on the evidence of 26 trades (2026-08-10)

Prompted by "pull all the live/closed trades for all the segments and
analyze them; improve the segment-wise algo as per our P7." 26 trades on
disk (10 closed, 16 open) and ~13,800 signals over the last three days.

**Yes, it is time to start P7 — but P7's two halves have to run in that
order.** ARCHITECTURE.md §19 scopes P7 as "real strategies per segment,
backlog features, chaos testing, alert tuning," exiting when "the first
strategy reaches `PAPER_FULL` through the real gates." Those gates
(`backtest.promotion.evaluate_track_b`) are closed-trade statistics, and
the closed-trade record was measuring a broken execution path rather than
a strategy. Calibrating a new strategy against it would have fitted the
plumbing. So P7 opens with the hardening half; the strategy half needs a
clean fortnight of trades underneath it first.

### 15a. Where the money actually went

Ten closed trades, **-Rs 9,462 net** (-19.0% of the Rs 50,000 NSE book;
`nse_stock` -8,614, `nse_index` -848). The `nse_stock` breaker tripped on
its daily loss limit at -17.2% and rejected 709 signals afterwards —
working exactly as configured.

The distribution is the finding, not the total:

| | trades | net |
|---|---|---|
| Never showed **any** favourable excursion | 4 | **-Rs 8,740 (92%)** |
| Worked, then gave it back or was forced out | 4 | -Rs 1,029 |
| Worked and banked something | 2 | +Rs 311 |

Excursion timing, measured off `position_marks`:

| id | underlying | exit | MFE(all) | MAE | at 90 session-min | net |
|---|---|---|---|---|---|---|
| 2 | HDFCBANK | TIME_EXIT | +0.7% | -29.9% | **-13.0%** | -3,554 |
| 23 | BAJFINANCE | STOP_LOSS | -5.0% | -30.1% | **-15.2%** | -2,686 |
| 7 | ADANIENT | TIME_EXIT | +2.3% | -25.9% | **+2.3%** | -1,490 |
| 21 | SBIN | STOP_LOSS | +0.2% | -31.5% | (stopped at 10 min) | -1,010 |
| 9 | Nifty 24650 C | STOP_LOSS | +14.8% | -21.0% | +2.2% | -411 |
| 4 | Nifty 24750 C | STOP_LOSS | +31.3% | -22.6% | +8.8% | -271 |
| 8 | WIPRO | TIME_EXIT | +21.8% | -6.3% | -4.8% | -185 |
| 3 | BANKNIFTY | TIME_EXIT | +19.1% | -9.4% | +10.5% | -166 |
| 24 | TATASTEEL | PARTIAL_R1 | +30.0% | -10.0% | +30.0% | +22 |
| 6 | SUNPHARMA | STOP_LOSS | +42.8% | -21.1% | +14.2% | +289 |

**Every trade that ever reached +10% did so within 82 session-minutes,
five of six within 16.** The four that never got there had a best-ever
excursion of +0.7%, +2.3%, +0.2% and -5.0% across their *entire* life,
then rode to a -30%-of-premium stop or a Monday-morning time exit. At the
90-minute mark the two groups do not overlap: the failures sat at -15.2%
to +2.3%, everything that worked at +8.8% or better.

That separation is what §15c's scratch rule trades on. It is an early
"this isn't it," not a prediction, and it costs the winners nothing.

### 15b. The instrument-identity bug — one contract, two rows

**Found while asking why trade 5 (MARUTI) had been logging
`TIME_EXIT could not fill (STALE_QUOTE) — quote 431,386s old` once a
minute, for hours.** 431,386 seconds is five days.

Two `instruments` rows existed for each real NSE option contract:

```
sync-instruments (T0):  "KOTAKBANK 385 PE 25 AUG 26"    <- Upstox trading_symbol
store_chain_snapshot:   "KOTAKBANK 385.0 P 2026-08-25"  <- synthesized in ingest.py
```

Identical `exchange`/`expiry`/`strike`/`option_type` and **identical
`provider_ids`** (`NSE_FO|115925`) — but the unique constraint keys on
`symbol`, which differs, so both rows were created. This is §6 #11's
duplicate-identity problem, flagged for the NIFTY index in P1 and never
chased down for option legs. **2,987 duplicate groups** exist.

It split each trade's life across two identities. `load_chain` sourced
candidates from whichever row the chain poll writes quotes to, while
`_resolve_leg_instrument_id` matched on (underlying, strike, type,
expiry), got two hits, and took whichever the database returned first.
The WS feed had been writing to the T0 rows until it was capped and then
lost its authorisation (§14e) — so from **2026-08-05 15:40** those rows
went silent while the chain poll kept updating the others.

Consequences, all silent, all live on 2026-08-10:

- **Every open `nse_stock` position was on a dead row.** Trades 5, 22, 25
  and 26 were being monitored against a quote last seen five days
  earlier.
- **Trade 26 (KOTAKBANK) had zero `position_marks` — ever.** Its row had
  never received a single quote, so `_latest_quote` returned `None` and
  `run_exit_tick` returned before evaluating anything. It carried **no
  stop-loss monitoring at all** for the entire session.
- **The marks were frozen prices restamped with a fresh `ts` every tick.**
  Trade 5 reported a mark of 181.05 and -Rs 458 unrealized at 15:29 on
  08-10; that price is from 08-05. Unrealized P&L, `equity_snapshots`,
  and therefore the breaker's own inputs were fiction for those
  positions.
- Trade 22 (ONGC) was *opened* at 09:19 on 08-10 — priced off a live
  quote, then immediately handed to a dead row.

**Root cause fixed** in `data.ingest.upsert_instrument`: match on the
provider's own instrument key first, falling back to the natural key. The
provider key is the identity; `symbol` is a label. Duplicates cannot be
recreated. `_resolve_leg_instrument_id` additionally orders by
most-recently-quoted — the money path must not go back to choosing
arbitrarily if a duplicate ever arrives from another direction.

**Every closed trade was on a live-quoted row**, checked explicitly — so
§15a's numbers, and the rules derived from them, are sound. The damage is
confined to the four open NSE positions.

### 15c. The four rule changes, each from the table above

1. **Time exits count session seconds, not wall-clock.**
   `core.sessions.session_seconds_between` / `session_length_secs`, and
   `max_holding_sessions` in each segment YAML. The 3-calendar-day guard
   fired all four TIME_EXITs on 08-10 between **09:17 and 09:19 IST** —
   positions opened the previous Wednesday/Thursday, four calendar days
   old but only two sessions old, forced out into the week's widest
   spreads. All four were losses (-Rs 5,395 between them).
2. **New `SCRATCH_EXIT`** (`scratch_exit_after_minutes: 90`,
   `scratch_exit_min_mfe_pct: 0.08`). If the high-water mark has not
   reached +8% within 90 session-minutes, leave. Uses the HWM, not the
   current mark, so a position that ran and gave it back belongs to the
   stop, not to this rule. Ordered last in `evaluate_exits` so it can
   never pre-empt a real exit.
3. **New `EXPIRY_EXIT`**, 30 minutes before the expiry session's close.
   Nothing in the exit rules referenced `expiry` at all; five open
   positions (MSFT/GOOGL/META/NVDA + QQQ) were holding contracts expiring
   **that same day**, protected only by the theta guard happening to fire
   first. Fixed cushion, not a knob — a mechanical margin, not strategy.
4. **First partial moves 1R -> 0.5R** (`(0.5, 1.0, 2.0)`). With a
   30%-of-premium stop, 1R means +30% before anything is banked. Only two
   of six winners ever touched it; all six cleared +14.8%. Trade 9 peaked
   at +14.8%, had no rung below +30%, and closed at -Rs 411.

Plus **`session_warmup_gate`** (`entry_warmup_minutes: 20`): 11 of the 13
NSE entries ever taken were opened within 20 minutes of the bell, 5
within 4 minutes, and they carry essentially all of the realised loss.
Five of the eighteen launch-set features — ATR, VWAP position, opening
range, volume-profile POC distance, price acceptance — are intraday by
construction, so a 09:18 confluence score is largely degenerate inputs
wearing a number. It also removes the structural half of §14c: without a
warm-up the engine met every scarce slot at once, in watchlist iteration
order, before the session had shown anything.

476 tests (11 new), ruff/mypy clean.

### 15d. Deliberately not done

- **`trades.mfe`/`mae`/`r_multiple` are still NULL on every row.** Left
  alone on purpose: no P7 gate reads them (`mfe_mae_ratio` is a *Track A*
  metric computed from backtest resolution, not from these columns), and
  every number in §15a was computed from `position_marks`, which already
  records every tick. Write them when something actually reads them.
- **`trades.premium_paid` decrements to 0 as a position closes** — it is
  "remaining cost basis", not "what was paid", so every closed trade
  reports 0. Real, but nothing downstream currently misreads it.
- **The scratch rule is not backfilled onto already-open positions.**
  Correcting the theta guard's *unit* on live trades is a measurement
  fix; applying a brand-new exit rule retroactively is not.
- **`base_risk_pct` untouched.** 8% x 5 concurrent slots is 40% of the
  book at risk, and the -19.85% drawdown followed from it — but 8% is
  ARCHITECTURE.md §11's own table, downstream of ADR 0005's explicit
  capital decision, and `exposure_cap_pct` (0.40) already bounds
  *concurrent* risk to ~12%. The lever the evidence supports is fewer bad
  trades, not smaller ones. Revisit with the user, not by drive-by.
- **US ingestion / Upstox WS** — unchanged since §13d/§14e. The feed still
  needs a token action, not code.

### 15e. Deployed and repaired (2026-08-10 18:29 IST)

Both live actions done, in this order while NSE was shut:

1. **Four open trades repointed** — 5 (MARUTI) 33105->47587, 22 (ONGC)
   15812->48106, 25 (TCS) 23439->46508, 26 (KOTAKBANK) 18944->46851 —
   `orders` rows with them, in one transaction. `max_holding_secs`
   corrected from 259200 wall-clock seconds to 67500 (3 NSE sessions) /
   70200 (3 US sessions) on all 16 open trades. All 16 now resolve to a
   live-quoted instrument.
2. All four engine units restarted onto `caac907` and confirmed active,
   idling correctly against a closed market.

**What the repair uncovered is worse than the frozen marks implied.**
Replaying the real `evaluate_exits` against every open position (read-
only) shows the four repaired trades are all far through their stops:

| trade | entry | reported mark | **real mark** | decision |
|---|---|---|---|---|
| 5 MARUTI | 199.37 | 181.05 (08-05) | **124.40** | STOP_LOSS |
| 22 ONGC | 2.95 | 3.00 (08-05) | **2.04** | STOP_LOSS |
| 25 TCS | 35.98 | 29.45 (08-05) | **24.90** | STOP_LOSS |
| 26 KOTAKBANK | 4.93 | *none, ever* | **2.80** | STOP_LOSS |

Unrealized on those four was being reported as -Rs 944 (with KOTAKBANK
contributing nothing at all, having no marks). The truth is **-Rs 2,880**
— `nse_stock` equity was overstated by roughly Rs 1,940, and the breaker,
the drawdown throttle and `risk_multiplier` were all reading the
overstatement. MARUTI alone was understated 4x.

All four will stop out at tomorrow's 09:15 IST open — which is precisely
the opening-bell moment §15c argues is the worst time to be a forced
seller. Noted rather than worked around: they are already deep through
their stops, the warm-up gate governs entries only, and there is no
better moment available to a position that should have closed days ago.

The session-time theta guard correctly does **not** fire on any of them
(MARUTI is 12.4 session-hours old against 18.75), so the stop is what
acts — the right rule winning, not two rules racing.

`EXPIRY_EXIT` gets its first real exercise tonight: NVDA, META, GOOGL and
QQQ all expire 2026-08-10, and the US session opens at 19:00 IST. They
read NO_ACTION now and should fire 30 minutes before the close (01:00
IST) — **unless US quotes are still 65h stale from the LSE weekly cap
(§13d), in which case the exits will correctly fail loud as
`EXIT_FAILED`/`STALE_QUOTE` rather than fill on fiction.**

### 15f. The LSE "quota burn" was two lying sensors, not a quota burn (2026-08-10)

Investigated because `feed_health.lse` read
`LSE options() quota exceeded: daily request limit reached (15000/day)`
at 18:55 IST, minutes before the US session opened — apparently the §13d
outage again, with four positions expiring that same day and no data to
exit them. **It was not happening.** Recorded because the way it looked
real is the reusable part.

Measured instead of assumed, in this order:

1. **Sampled `feed_health` every 3s during the live session.** `connected`
   steady, `subscribed_count` climbing 591 -> 649, `last_message_at` fresh
   every 2-3s. 3,209 `us_stock` + 1,268 `us_index` quotes landed in five
   minutes, and all five contracts expiring that day had quotes seconds
   old. The feed was healthy the entire time.
2. **`last_error_at` was `08-07 01:28:18` — three days stale.** Nothing
   ever cleared `last_error`/`last_error_at`, so the columns mean "the
   last error ever seen" while every reader treats them as "what is wrong
   now". The 65h-stale US quotes seen earlier were simply the weekend.
3. **Called LSE's own `/usage` directly.** The real payload has no
   `used_pct` and no `used` — it is a set of budgets:
   `bytes_used_month 774,463,437 / bytes_cap_month 53,687,091,200`,
   `bytes_used_week 774,463,437 / bytes_cap_week 16,106,127,360`,
   `exports_this_hour 0 / exports_cap_hour 5`, `calls_per_minute 200`.
   **Real headroom: 4.8% of the weekly cap.** §13d's remediation worked.

**Both sensors fixed:**

- `lse/client.py::quota()` — `raw.get("used_pct") or raw.get("used")` was
  always `None` against the real payload, and the `else` branch returned a
  hardcoded **0.0**. §13d fixed the *failure* path (429 -> 100.0) and left
  the *success* path doing exactly what `QuotaStatus`'s own docstring
  forbids: conflating "unknown" with "zero". It now derives the percentage
  from whichever budget is fullest (the one that will actually stop us —
  max, not average; `calls_per_minute` excluded, a rate is not a
  consumable) and degrades to `None`, never 0.0, when nothing parses.
  5 tests, including the real captured payload.
- `recorder.py::update_feed_health` — a healthy update now clears
  `last_error`/`last_error_at`. Cleared at the source rather than compared
  at read time (`last_error_at > last_message_at`), because there is more
  than one reader and a comparison every reader must remember is one a
  reader will eventually forget. The text stays in the journal; the column
  is a status light, not a log.

**Verified live end to end** after deploying: both providers' stale
`last_error` cleared to NULL on the next healthy update, and a completed
T1 cycle wrote a real `quota_used_pct` — 5.18%, against the hardcoded
0.00 it had reported for as long as the column has existed. It also
moves (5.07% -> 5.18% across a few minutes of live session), which is the
actual test: a number that never changes is indistinguishable from a
number that is not being measured.

**Worth generalising, a third time.** §13d: a fault reading green. §14: a
failure with no way to report failing. This one is the mirror — **a
recovery that reads red forever** — and it is the same root defect as
§13d's, in the same function, on the branch §13d did not touch. A sensor
whose "healthy" and "unknown" values are the same number is not a sensor.
When a status field disagrees with the thing it describes, sample the
thing, not the field.

### 15g. Intraday only — nothing is held overnight (user's call, 2026-08-10)

Requirement, stated plainly: every position is closed by the end of its
own session, or earlier on its target or stop. Three changes, and the
third prevents a bug the first two would otherwise have created.

**1. `monitor.session_close_exit_check`** — two ways a position can end up
carrying, so two checks:

- `EOD_EXIT` — `now` is inside the closing cushion. **15 minutes, not 1
  or 2.** An exit is a fill request that can be refused (§14a is the whole
  story of stops that could not fill), the engine ticks once a minute, so
  15 minutes buys roughly 15 attempts. A tighter cushion trades some of
  the session's last move for a real chance of still being long at the
  bell.
- `OVERNIGHT_EXIT` — the position was opened on an earlier session date
  and is still open. The cushion check cannot catch this: the engine idles
  while the market is shut, so if it was down or an exit failed during the
  final minutes, the next morning's ticks are nowhere near a close and the
  straggler would be held all the following day.

**2. `session_timing_gate`** (was `session_warmup_gate`) grew a closing
edge, `entry_cutoff_minutes: 90`. With everything intraday, a position
opened near the bell is bought and sold for the two spreads — it gets
force-closed whatever it is doing. 90 minutes is the same number as the
scratch window and comes from the same measurement: every closed trade
that ever worked reached +10% within 82 session-minutes, so less than 90
minutes of runway is less than the evidence says a trade needs. Live
examples this stops: trade 27 opened 15:09 IST (21 minutes before the
close), trade 9 at 14:58. The NSE tradable window becomes 09:35-14:00,
265 of 375 minutes.

**3. The ordering is load-bearing.** `session_close_exit_check` runs
*ahead of every optional exit*, because `r_multiple_partial_exit_check`
returns a **fraction** of the position — if it had won a tick inside the
closing cushion, the remainder would have been carried overnight, the one
outcome the rule exists to prevent. Pinned by
`test_eod_exit_outranks_the_partial_exit_that_would_strand_a_remainder`,
which asserts the partial really would have fired first. Stop-loss and
trailing stop still outrank it: risk protection stays first, and the
recorded `exit_reason` must be the one that actually fired. The cost is
attribution only — a winner closing at 15:20 is labelled `EOD_EXIT` rather
than `PROFIT_TARGET`; both fully close at the same price.

**Two test fixtures were quietly wrong and this exposed them.** Both
`test_gates.py` and `test_monitor.py` used `_NOW = 10:00 UTC` — which is
15:30 IST, the closing bell exactly. Harmless until a rule cared about the
close, at which point every baseline in both files was an entry or a
position at the bell. Both moved to 11:30 IST. `test_gates.py`'s
all-segments baseline also had to start deriving a per-market instant:
NSE (03:45-10:00 UTC) and US (13:30-20:00 UTC) do not overlap, so no
single `now` is mid-session for every segment any more.

`max_holding_sessions` and `expiry_exit_check` are both now dominated by
this rule in practice — nothing survives a session, so neither can fire.
Kept rather than deleted: they are a handful of tested lines and they are
the backstop if the intraday rule is ever relaxed. 488 tests.

### 15h. No US position had ever been exitable (2026-08-10)

Found within a minute of deploying §15g, because the intraday rule
started demanding exits that had to actually work. Every US position
logged, every tick:

```
trade 17: OVERNIGHT_EXIT could not fill (NO_LIQUIDITY_AT_TOP_OF_BOOK)
trade 10: STOP_LOSS      could not fill (NO_LIQUIDITY_AT_TOP_OF_BOOK)
```

`run_exit_tick` built its `QuoteSnapshot` with
`bid_sz=latest_quote.bid_sz or 0`. **LSE publishes no bid, no ask, and no
sizes at all** (§13c), so that fallback was always 0 — and
`execution.fills` fills `floor(partial_fill_alpha * size)`, which is 0 for
any size below 4. So every US exit attempt, stop-losses included, was
rejected on every tick since the first US trade opened on 2026-08-07.
**US positions could be entered and never closed.**

§13e built `synthesize_quote` for the *entry* path only, and §13c
explicitly recorded that "the exit path already degrades gracefully
(`bid=latest_quote.bid or mark`)". That observation was half right: it
fixed the *price* and left the *size* at zero, which can never fill. The
asymmetry it noted was real and pointed the wrong way.

Fixed with `orchestrator._exit_quote`, which mirrors the entry path: US
exits are priced against a modelled book from ltp + volume, NSE keeps the
observed one. Still gated on `Market.US` rather than "synthesize whenever
sizes are missing" — §13e's rule, so an NSE feed hiccup can never quietly
move a real-book segment onto modelled prices. When a contract is too thin
to model honestly, `_exit_quote` returns None and the caller logs
`EXIT_FAILED`/`NO_SYNTHETIC_BOOK`: reported, never invented.

**The intraday rule turns this into a guarantee rather than a hope.**
`_candidates_from_chain` already refuses a contract whose modelled size
cannot fill a lot, and same-day volume only ever grows — so a position
that was enterable this session is exitable this session. That property
does not hold across days, which is exactly the regime being abandoned.

**Verified live, two ticks after deploying.** Six US positions closed —
3 `OVERNIGHT_EXIT`, 3 `STOP_LOSS` — against zero that had ever been able
to close before. The engine also opened two fresh US trades (28 DIA, 29
INTC) under the new gates in the same session, so entries and exits are
both working.

Five legacy positions stayed open, each refused honestly rather than
faked: NVDA (volume 1, ltp 0.01), QQQ (volume 3), ORCL (19), JPM (34) are
all below the volume that models a fillable book, and XOM tripped
`SPREAD_TOO_WIDE`. These are 08-07 entries on contracts that are now
nearly dead — the intraday rule cannot retroactively rescue positions
taken under the old regime, and going forward the entry gate prevents the
situation arising.

**One residual edge, flagged not fixed:** `synthesize_quote`'s spread has
a tick floor (0.01 below $3), so on a very cheap contract the modelled
spread exceeds `fills.py`'s 500bps `SPREAD_TOO_WIDE` policy — XOM at ltp
0.16 models a 625bps spread. A position would have to decay to roughly a
tenth of its entry price intraday to reach that, which the -30% stop
should catch first, so this is a narrow case. Revisit if a live exit ever
gets stuck there.

Worth noting what found it: not a test, and not review. The bug was three
days old and silent behind `NO_LIQUIDITY_AT_TOP_OF_BOOK` warnings nobody
was reading. It surfaced because a new *requirement* made a previously
tolerable failure intolerable — the same way §14a's stop-loss failures
surfaced only once someone asked what the closed trades did. 492 tests.

### 15i. LSE, assessed properly — US P&L is not currently trustworthy (2026-08-10)

Asked plainly ("is LSE giving us a problem?"). Measured, not recalled.

**1. It publishes no order book. At all.** Of **541,234** US option quotes
recorded in 24h: `bid` present on **0**, `ask` on **0**, `bid_sz` on **0**.
`ltp` on all 541,234, IV on 329,629. For contrast, NSE/Upstox over the
same window: 9,800,490 quotes, `bid` on 9,800,399 and `bid_sz` on
**100%**. This is not a gap to be fixed — it is what the vendor sells.
Every US fill price this system has ever produced is modelled
(`synthesize_quote`, `SPREAD_PCT = 4%`, an assumption with nothing to
calibrate against), and §13c/§13e/§15h are all downstream of this one
fact.

**2. Only 20.5% of the live US chain is tradeable** — 1,358 of 6,627
quoted legs carry enough volume to model a fillable book. Four fifths of
the chain is invisible to the selector.

**3. The `volume` column carries two different meanings**, depending on
which writer produced the row, and `synthesize_quote` divides by it to
size the book:

| writer | rows tonight | `volume` means | median |
|---|---|---|---|
| WS stream (`snapshot_id` NULL) | 195,608 | that single print's size | 2 |
| REST chain (`snapshot_id` set) | 345,309 | cumulative daily volume | 10 |

So the top-of-book proxy is fed per-trade sizes on 36% of rows and
day-cumulative totals on the rest. That is why exits flapped between
`no modellable book` and filling normally within the same minute.

**4. The REST chain serves frozen prices, and it cost a real trade.**
QQQ 725 C (trade 20) tonight, same instrument, split by writer:

```
WS prints  : 2,078 rows,  ltp 0.16 - 1.43,  volume 1 - 750
REST chain :    52 rows,  ltp 2.1500 EVERY ROW,  volume 25969 EVERY ROW
```

Fifty-two consecutive polls across the whole evening returned an
identical price and an identical volume while the live market moved from
1.43 down to 0.16. **Trade 20 then exited at `avg_exit = 2.1242`**,
priced off the frozen chain value, and booked **-$197**. Against the
live prints (~0.18) the real loss is roughly **-$2,120**. The trade is
recorded about 10x better than it was.

This is §15b's failure shape — a stale price wearing a fresh timestamp —
except sourced from the vendor rather than from our own duplicate-row
bug, and it survives every staleness check we have because `ts` is
genuinely current.

**Consequence: US closed-trade P&L cannot be used for anything**, and
that includes P7's Track B promotion gates, which are closed-trade
statistics. NSE is unaffected — Upstox publishes a real book on 100% of
rows.

**Recommended: halt both US segments** (the per-segment manual breaker
from §12) until the writer conflict is resolved, rather than keep booking
fictional fills. The fix is not obvious enough to land inside a live
session: it needs a decision about which writer is authoritative for a
mark, and probably that WS prints — not the chain poll — own `ltp` for
US, with `volume` split into two columns that mean what they say.

### 15j. Full reset — both segments, user's explicit call (2026-08-11)

Requirement, stated plainly: clear every open NSE position (they were not
intraday — still open from before §15g shipped) and reset both NSE and
US entirely, so performance is judged fresh against everything fixed in
§15a-15i rather than mixed in with the trades that measured the broken
plumbing.

Asked before touching anything, since "reset" forked into materially
different actions (administrative close vs. waiting for the market's own
real fill; keep history with a cutoff marker vs. delete it). User's
answer, all three questions: **delete everything, both segments, reset
capital too.**

**Backed up first** — `pg_dump` of every affected table to
`/root/backups/pre_reset_2026-08-11.sql` on the VM (838KB, 3,541 lines)
before deleting anything, despite the explicit instruction. Costs nothing
and keeps an irreversible action recoverable.

**Deleted, in FK order** (children first): `trade_events` (2,771),
`fills` (61), `position_marks` (10,618), `orders` (61), `trades` (32),
`equity_snapshots` (9,448), `risk_state` (4). All zero afterward.
`signals` deliberately left untouched (54,175 rows) — it is the rejected-
signal record, not P&L, and nothing about "reset the performance" asked
for the evaluation history to go.

**Capital resets by construction, not by writing a number.**
`risk.accounting.update_equity_and_risk_state` recomputes `cash`/`equity`/
`high_water_mark` fresh from `trades`/`position_marks` on every tick — an
empty `trades` table means `realized = 0`, `equity = config.capital`
(₹50,000 / $50,000 per segment), and a missing previous `equity_snapshots`
row means `high_water_mark` starts at `capital` rather than inheriting
yesterday's peak. Clearing `equity_snapshots`/`risk_state` themselves (not
FK'd to `trades`, so not touched by the cascade) just makes that visible
immediately instead of at the next tick.

**Worth being explicit about:** this ran via the `kairodex` superuser
role, the same one P3's live security test used to *prove* tampering is
detectable (`event_log.verify_chain` correctly returning `False` after an
admin-level edit — see §9). `trade_events` is deliberately revoked from
`kairodex_app`, the role the live engine itself runs as, specifically so
the app can never rewrite its own history. That guarantee protects
against silent tampering by the running system, not against an explicit,
authorized reset by the person who owns the paper-trading experiment —
but it means this class of action can only be done the way it just was:
by hand, off the app's own credentials, with a paper trail. It is not
something the engine could ever do to itself.

**Engines restarted** on the empty book — all 4 active. Both markets are
currently closed (NSE opens 09:15 IST, US tonight 19:00 IST), and
`update_equity_and_risk_state` only runs inside the session-open branch of
`live_loop.run_segment` — so `equity_snapshots`/`risk_state` will read
empty until each market's first tick, not immediately. `risk.loader`'s
`AccountState` builder already defaults cleanly against an empty table
(same pattern as every other DB-touching loader in this codebase), so
nothing is broken in the interim — the dashboard will show a gap rather
than a wrong number until 09:15.

**From this point, every trade in the system was opened under §15a-15i's
fixes** — the identity bug, the session-time exits, the scratch rule, the
warm-up/cutoff gates, the intraday-only rule, and the US exit-quote fix.
Today's NSE session (2026-08-11) is the first clean measurement.

---

## 16. P7's strategy half — the NSE detectors, calibrated (2026-08-11)

Prompted by "focus on hardening the algo for both NSE segments so when
the market will open we will have improved algo." Shipped and deployed
before the 09:15 IST bell; both NSE engines restarted onto it at 08:41.

**§15j's reset did not have to block this.** The blocker §15 named was
calibrating against *closed trades*, and that is still impossible — see
§16d. But `signals` was deliberately left intact through the reset
(54,175 rows, 20,399 of them NSE), and `signals.evidence` carries **every
detector's score on every evaluation**. That is a calibration surface of
20,399 real observations that needs no trade, no fill, and no P&L. It was
sitting in the database the whole time.

### 16a. The scorer was not discriminating at all

One measurement makes the case:

> **100.0% of all 18,977 real `nse_stock` evaluations produced a
> direction.** Not 99%. Every single one.

A confluence rule that never abstains is not a filter. Two independent
causes, both found by replaying the stored evidence.

**1. `trend_structure`'s scale was ~27x too large.** Its `ponytail:`
comment assumed `|trend_state_strength| ~0.001-0.15` and set
`_SCALE = 0.05`. Inverting `tanh` over all 20,399 evidences gives the
real live distribution — and both NSE segments agree closely:

| segment | p50 | p75 | p90 |
|---|---|---|---|
| nse_stock | 0.00061 | 0.00137 | 0.00188 |
| nse_index | 0.00066 | 0.00142 | 0.00165 |

The top of the assumed range was ~80x high. Every reading therefore
scored `|0.016|` on a [-1, 1] scale — mean 0.017, p90 0.038, nothing
saturated. **The STRUCTURE family cast a full confluence vote while
contributing essentially nothing to confidence**, because direction is
decided by family *count* and confidence only by the scores afterwards.
Now `_SCALE = 0.0018`, its own p90, which is the same `p90 -> tanh(1)`
convention `relative_strength` already followed (`_SCALE = 0.03` against
its measured p90 of 0.022 — the one detector whose scale was right, and
the only one with a healthy distribution: p50 0.323, 0% saturated).

**2. `agreement_threshold` was 0.0**, so a family reading `+0.002`
counted as agreeing with one reading `+0.98`. Now **0.20**: a family must
hold a real opinion to be counted, not merely a non-zero one.

Replaying all 20,399 signals through both fixes:

| segment | evaluations signalling, before | after |
|---|---|---|
| nse_stock | **100.0%** | 43.3% |
| nse_index | 67.6% | 21.0% |

The threshold also **retires `relative_strength` on `nse_index` by
construction rather than by special case** — an index measured against
its own benchmark reads p50 `|0.002|` there (vs. 0.323 on `nse_stock`),
so it now abstains instead of casting a noise vote. No `if segment ==`
anywhere; the floor does it.

### 16b. `min_confidence` had to move with it, and that is not optional

Both NSE values were the p75 of the **old** confidence distribution. The
rescale moves that distribution, so leaving them would have passed nearly
everything — a stricter scorer wired to a now-trivial gate. Recomputed at
the p75 of the **replayed** distribution:

| segment | was | now | new p50 / p75 / p90 |
|---|---|---|---|
| nse_stock | 0.35 | **0.71** | 0.628 / 0.712 / 0.789 |
| nse_index | 0.30 | **0.32** | 0.306 / 0.317 / 0.326 |

The two are no longer ~10x apart. Most of that old gap *was*
`trend_structure`'s broken scale. What remains (0.32 vs 0.71) is real:
`nse_index` now has only two live families, so its confluence mean is
structurally lower.

Still a percentile of a live distribution, **not an outcome-fitted
number** — the honest ceiling on everything in this section.

### 16c. Not fixed: `oi_price_flow` has never once fired

`avg_detectors = 3.00` exactly, across all 20,399 NSE signals; the FLOW
family appears in **zero** of them. So `min_families = 2` has always been
2-of-**3**, never 2-of-4 — and until today one of those three was noise,
making it effectively 2-of-2 with a tiebreaker. `oi_change` is presumably
never populated in the live feature path, but the root cause is not yet
found, and a detector that has never run is not something to fix by
guessing 20 minutes before a session. Flagged, deliberately not touched.

Also left: `iv_skew_sentiment` is not mis-scaled but **heavy-tailed** —
27.5% of readings score `<0.01` and 33.2% saturate at `>0.99`, from the
same `_SCALE = 5.0`. That is a shape problem in the underlying feature
(put IV - call IV, presumably one leg occasionally garbage), not a
constant to retune. Needs the feature looked at, not the detector.

### 16d. What this is not

**It is not calibrated against outcomes, because it cannot be yet.**
Restoring `/root/backups/pre_reset_2026-08-11.sql` into a scratch DB and
replaying the current gates over its 15 NSE trades:

| verdict under today's rules | trades | net |
|---|---|---|
| REJECT — warm-up (before 09:35) | 12 | -Rs 9,051 |
| REJECT — cutoff (after 14:00) | 2 | -Rs 411 |
| **ALLOWED** | **1** | — |

**14 of 15 would never have been taken.** The single survivor is trade 26
(KOTAKBANK, 09:57) — §15b's dead-row trade, which received no quote ever
and never closed. Effective NSE sample under the current ruleset: **zero
closed trades.** Every rupee of the -Rs 9,462 was earned in the first
nine minutes of a session the engine no longer trades, under exits since
rewritten. §15's "wait for a clean fortnight" still stands for anything
outcome-fitted; this section deliberately fits only to *distributions*,
which is why it could run today.

### 16e. The backup silently lost two tables

Found while restoring it. `pg_dump` reported success, wrote 838KB, and
contained **zero rows** for `position_marks` (10,618) and
`equity_snapshots` (9,448) — the only two of the seven reset tables that
are **TimescaleDB hypertables**. Their rows live in `_timescaledb_internal`
chunks, which a `-t`-scoped dump never visits; the parent table dumps as
an empty `COPY`. §15j's row counts came from `SELECT count(*)` before the
delete, not from the dump. Those 20,066 rows are gone.

**Use `COPY (SELECT * FROM x) TO ...` for hypertables** — it reads
through the parent and cannot miss chunks.

**Partly recovered anyway, from the event log.** The trailing stop sits
at `hwm x 0.70` and every ratchet writes a `STOP_MOVED` event, so
`HWM = new_stop_price / 0.70` — and `trade_events` (2,771 rows) did
survive. That reproduces §15a's MFE column **exactly on all 8 checkable
trades** (+42.8, +31.3, +21.8, +19.1, +14.8, +2.3, +0.7, +0.2%). MAE is
not recoverable — the stop never ratchets down, so nothing records the
low.

**Worth generalising, a fourth time.** §13d: a fault reading green. §14:
a failure with no way to report failing. §15f: a recovery reading red
forever. This one is a **success that was partly a no-op** — and it is
the same defect once more: the thing reporting the outcome was not the
thing doing the work.

### 16f. Status

492 tests (1 new), ruff/mypy clean. One **pre-existing, unrelated**
failure: `test_expiry_cache.py::test_bars_refresh_when_the_newest_is_
older_than_max_age` asserts `end > date.today()` and fails on any run
dated 2026-08-11 — it fails identically on the parent commit (verified by
stashing), is US/LSE-scoped, and is untouched by this work.

**US was deliberately not restarted.** The scorer and detector changes are
global, but only the NSE `min_confidence` values were recalibrated — so a
US engine restarted onto this commit would run the stricter scorer against
a gate set against the *old* distribution, i.e. looser than before. The
running US processes hold the old code in memory and are unaffected, but
**any restart or reboot before 19:00 IST puts them on the new scorer with
stale gates.** Either halt both US segments (§15i already recommended it,
for the unrelated and stronger reason that their fills are modelled off a
vendor with no order book) or recalibrate their `min_confidence` the same
way. Not decided here.

---

## 17. The first clean session, and the scorer fails its first real test (2026-08-12)

Prompted by "analyze todays closed trades on NSE and see how we move
forward for P7 in hardening the segments and their algos." Five closed
NSE trades — the first ones ever taken entirely under §15a-16's rules,
since §15j reset the book and §16 shipped before the 08-11 bell.

**+Rs 1,591 net** (`nse_stock` +235 / +0.47%, `nse_index` +1,356 /
+2.7%). Both breakers ARMED throughout.

| id | underlying | contract | open→close IST | MFE | MAE | exit | net |
|---|---|---|---|---|---|---|---|
| 60 | BANKNIFTY | 58200 CE | 09:47→14:49 | +15.1% | -12.6% | PARTIAL_R0.5 | +1,356 |
| 61 | INFY | 1175 P | 10:11→15:16 | +43.4% | -0.6% | EOD_EXIT | +2,239 |
| 62 | TCS | 2340 P | 10:57→12:00 | **+105.8%** | +17.6% | PROFIT_TARGET | +4,020 |
| 63 | TCS | 2280 P | 12:01→12:38 | **-6.2%** | -31.8% | STOP_LOSS | -4,343 |
| 64 | TCS | 2300 P | 13:09→14:40 | +0.7% | -17.7% | SCRATCH_EXIT | -1,680 |

### 17a. The hardening itself held

Nothing from §15-16 misfired. Earliest entry 09:47 against a 09:35
warm-up boundary; everything flat by 15:16 with no carry; all five trades
carried continuous `position_marks` from within a minute of entry, so
§15b's dead-row failure is genuinely gone. The scratch rule earned its
keep on trade 64 — MFE +0.7% at 90 session-minutes, out at -17.7% instead
of riding to the -30% stop.

The rejection mix is the part worth keeping: 869 `BELOW_MIN_CONFIDENCE`,
92 `ALREADY_OPEN_ON_UNDERLYING`, 74 `EXPOSURE_CAP_EXCEEDED`, 47
`SESSION_CLOSING`/`WARMUP`, 19 `REENTRY_COOLDOWN_ACTIVE` — and exactly
**3 plumbing rejections** (1 `STALE_QUOTE`, 2 `NO_TRADE_MIN_SIZE`) out of
1,117 evaluations. A 0.4% take rate is selectivity; the thing to watch is
whether rejections are decisions or breakage, and today they were
decisions.

### 17b. A winning close was a free pass to re-enter — fixed

Trade 62 closed **+Rs 4,020 at 12:00:08**. Trade 63 opened on the same
underlying, same direction, one strike lower, at **12:01:08** — the very
next tick — and lost -Rs 4,343. Trade 64 then opened at 13:09:13, sixty-
three seconds after the cooldown from 63's loss expired, and lost another
-Rs 1,680.

**TCS net -Rs 2,003, having been +Rs 4,020 up.** Without the two re-
entries the day is +Rs 6,258 on `nse_stock` rather than +Rs 235.

`risk/loader.py` built the cooldown map from `Trade.net_pnl < 0`, so only
losses populated it. The gate worked exactly as written — 63's loss did
produce a clean 30-minute block — but as written it was a revenge-trading
guard, and the risk actually being managed is re-entering a move you just
exited, which does not care which side of zero the exit landed on.

Fixed: `last_loss_ts_by_underlying` → `last_close_ts_by_underlying`, the
`net_pnl` filter dropped. **Live-verified on the VM** by building a real
`AccountState`: INFY, BANKNIFTY, ICICIBANK and BAJFINANCE — all winners,
all previously absent — now appear in the map.

Not fixed, and worth naming: this alone would not have saved the day.
TCS kept scoring 0.79-0.94 continuously until 14:00, so the engine would
very likely have re-entered at 12:31 when the cooldown lapsed. A per-
underlying per-session entry cap is the other half, and it is a knob with
no evidence behind it yet, so it was left alone.

### 17c. §16b's calibration did not survive contact with live data

Both NSE `min_confidence` values were fitted at the p75 of a *replayed*
distribution. Two real sessions under the new scorer say the replay was
wrong about both segments:

| segment | gate was | live p50 | live p75 | gate actually sat at | now |
|---|---|---|---|---|---|
| nse_stock | 0.71 | 0.4269 | 0.6041 | ~p85 | **0.60** |
| nse_index | 0.32 | 0.6043 | 0.6117 | ~p2 | **0.61** |

`nse_index`'s gate was **inert** — 5 confidence rejections out of 106
evaluations on 08-12, with the exposure cap doing all the real filtering.
§16b's specific prediction that `nse_index` sits structurally lower than
`nse_stock` because only two families stay live there is also wrong live:
the two p75s are now within 0.008 of each other.

Re-sited both. The p75 convention exists so selectivity stays fixed while
the scorer drifts — which means re-measuring weekly against live signals,
not fitting once. **§17d then undermined the convention itself**, see
below.

### 17d. `signals.forward_outcome` now exists — and says confidence is worse than useless

The column ARCHITECTURE.md §5.4 describes as "filled in later: did the
move happen?" had **never been written: 0 of 63,335 rows.** It is the one
calibration surface that needs no fills, no P&L and no closed-trade
volume, and it was sitting empty while §15/§16 both recorded "wait for a
clean fortnight" as the blocker.

New `backtest/backfill.py` + `kairodex backtest backfill-outcomes`,
reusing Track A's `resolve.resolve_forward_outcome` **verbatim** — the
same stop/target walk in ATR units, over each live signal's own
underlying 1-minute bars. Two rules that are not obvious and are pinned
by tests: forward bars are truncated at the signal's own session date
(the engine is intraday-only, §15g, so a 15:20 signal must be scored on
the ten minutes it actually had), and a truncated window that hit neither
stop nor target is left **NULL rather than scored as flat**, which would
otherwise systematically label every late-session signal as a
non-event.

Backfilled: **21,128 NSE signals** (19,729 `nse_stock` + 1,399
`nse_index`; 445 unresolved, 1,196 without 1m history).

**First sanity check, and it validates the resolver.** With a 1-ATR stop
and a 2-ATR target and ties resolving to the stop, a driftless random
walk hits its target **33.3%** of the time. Across all 19,729 `nse_stock`
signals the observed rate is **31.8%**. The instrument reads the
no-edge baseline correctly, which is what makes the next table mean
something.

| `nse_stock` band | n | sessions | avg return (ATR) | target hit |
|---|---|---|---|---|
| all signals | 19,729 | 5 | -0.046 | **31.8%** |
| conf >= 0.60 (the new gate) | 1,502 | 5 | -0.115 | **29.5%** |
| conf >= 0.71 (the old gate) | 741 | 5 | -0.279 | **24.0%** |
| conf >= 0.80 | 247 | 4 | -0.393 | **20.2%** |
| conf >= 0.90 | 102 | 3 | -0.235 | **25.5%** |

**Every confidence filter makes the forward outcome worse, monotonically
to 0.80.** Filtering on confidence is not a weak edge; it is a negative
one. The unfiltered population is already at the no-edge baseline, and
the gate selects away from it.

Split by scorer era, the effect appears independently in both — old
scorer conf>=0.8: 22.7% target on n=97; new scorer conf>=0.8: 18.7% on
n=150 — so it is not an artifact of §16's rescale.

The mechanism was already visible in the trade record before the backfill
confirmed it. Trade 63 entered on `trend_structure -0.965`, i.e. `tanh`
all but saturated, one minute after trade 62 took profit on the same
move at -0.813. Confidence is monotone in *extension*, so the scorer is
structurally most certain exactly where an intraday move is most
stretched. **It is an extension meter, not an edge estimate**, and §16b's
p75 convention was fitting a gate to select the top quarter of that
meter.

Deciles across the full range are flat (30.5%-34.0%, no trend), so the
damage is concentrated in the tail rather than being a smooth inversion.

**What this does NOT license.** Five sessions, 20 underlyings, and
signals re-evaluated every ~90 seconds share nearly all of their forward
window — 19,729 rows is nothing like 19,729 independent observations, and
the >=0.80 band spans only 4 sessions. This is enough to stop trusting
confidence, not enough to invert it. Inverting a noisy signal is how you
fit noise twice.

`nse_index` is untouched by this conclusion: n=29 above the new gate
across 2 sessions is not a measurement.

### 17e. Accordingly, the gates re-sited in §17c are provisional

The `nse_stock` move (0.71 → 0.60) happens to travel in the direction
§17d supports (-0.279 → -0.115) but for the wrong reason — it was a
percentile convention, not an outcome fit. The `nse_index` move (0.32 →
0.61) is a *tightening* toward the region that §17d finds harmful on the
other segment, on n=29 of its own evidence. Both stand for now because a
gate sited at a live percentile is still better than one sited at a
replayed percentile, but the honest state is: **the quantity these gates
filter on has no demonstrated predictive value.**

The next real question is not where to put the threshold. It is whether
`ConfluenceScorer`'s output should gate entries at all, and what an
extension-damped detector (score peaking mid-move rather than at the
extreme) does to the same table.

### 17f. Deliberately not done

- **Winner clipping.** The R-ladder sold **12 of trade 62's 13 lots in
  its first 7 minutes** (rungs at +18%/+34%/+65%) and the last lot ran to
  +106%. But the same ladder beat holding-to-EOD by Rs 206 on trade 61.
  Mixed evidence on two trades; not a tuning target yet.
- **The trailing stop cannot protect a profit below +43%.** It trails 30%
  under the high-water mark, so trade 61 — which peaked at +43.4% — had a
  trailed stop of 19.88 against a 19.81 entry, i.e. breakeven, and gave
  back half at EOD. Real defect in shape, needs more than five trades to
  size.
- **`nse_index`'s `max_concurrent: 2` is arithmetically unreachable.**
  BANKNIFTY premium was Rs 9,139/lot against `exposure_cap_pct` 0.30 x
  Rs 50,000 = Rs 15,000, so one position blocks the segment (74
  rejections). Cost nothing today; the config claims capacity it cannot
  have.
- **A per-underlying per-session entry cap** — the other half of §17b,
  see there.

### 17g. Status, and one hazard this session armed

498 tests (5 new), ruff/mypy clean, both locally and on the VM.
`test_expiry_cache.py`'s §16f failure is gone — it was date-dependent and
only failed on 08-11. Commit `e240c36`; NSE engines restarted onto it at
16:40 IST against a closed market.

**That commit also swept in two unrelated changes that were sitting
uncommitted in the working tree at session start** — `recorder.py`'s
feed_health liveness fix and §14e/§14e-bis's write-up (a `git add -A`
that should have been selective). Both are wanted and correct; the commit
message just does not mention them. **The ingest units were NOT restarted,
so the feed_health fix is on disk but not running** — restart
`kairodex-ingest-{nse,us}` to pick it up.

**The §16f US hazard is now armed rather than latent.** US engines still
hold pre-`0643e72` code in memory and were deliberately not restarted,
but the VM's checkout has moved twice since. `Restart=always` means a
crash or reboot now brings them up on the new scorer against
`min_confidence` 0.25/0.20 — gates fitted to the old distribution, i.e.
wide open. Live US p50 confidence under the *old* scorer is 0.151; the
new scorer shifted both NSE segments' distributions sharply upward.
Still undecided, and now with a deadline attached: halt both US segments
(§15i's recommendation, for the independent and stronger reason that LSE
publishes no order book at all) or recalibrate their gates.

---

## 18. P7-A — the FLOW family had never fired once (2026-08-12, evening)

§16c flagged it and deliberately did not chase it: `avg_detectors = 3.00`
exactly across all 20,399 NSE signals, with the FLOW family appearing in
**zero** of them, so `min_families: 2` has always been 2-of-**3**, never
2-of-4. §17 picked it as the first strategy-half task on the reasoning
that adding a genuine fourth family beats retuning three that §17d
measured at the no-edge baseline.

### 18a. Root cause: a parameter nothing supplied

```
live_loop.run_segment  ->  run_entry_tick(...)          # no prior_as_of
run_entry_tick         ->  build_context(prior_as_of=None)
build_context          ->  prior_chain = []
oi_change feature      ->  None          (needs ctx.prior_chain)
oi_price_flow_detector ->  None          (returns early on oi_change is None)
```

Every tick, since the engine was built. This is **exactly** the shape of
the `index_bars` gap that had `relative_strength_detector` permanently
dead — a `build_context` parameter that nothing anywhere passed — and
`run_entry_tick`'s own comment describes that earlier instance three
lines above the call that had this one. Two detectors of four have now
been found dead by the same mechanism.

**Generalisable, and worth stating as a rule:** an optional parameter on
a context builder is a silent-failure surface. Both dead detectors
degraded to `None` politely, were counted as "not applicable" by
`compute_all`, and produced no warning anywhere. The reason it took
20,399 signals to notice is that `avg_detectors = 3.00` had to be
computed deliberately — nothing in the system treats "a registered
detector has never once fired" as an error. It should.

### 18b. Plumbing it alone would have shipped a bad detector

Two independent problems, both measured against **15,739 real NSE chain
snapshots** rather than assumed:

1. **The price leg spanned the entire `underlying_bars` window** — 5 days
   of 1m bars per `features/loader.py` — against `_PRICE_SCALE = 0.01`.
   A 5-day NSE move is routinely 2-5%, so `tanh` would have saturated at
   ±1.0 on nearly every evaluation: a family casting a full confluence
   vote on a degenerate number, which is precisely the `trend_structure`
   pathology §16a diagnosed. The convention in the detector's own
   docstring compares OI and price over the *same* interval, and the code
   never did.
2. **The OI leg's sign is a coin flip at short horizons.** The detector
   reads only `oi_change > 0` (buildup vs. unwind, conviction 1.0 vs
   0.5), so only the sign matters — and chain-total OI change is 53.5%
   positive at 1 minute. It separates as the window widens:

   | horizon | % OI change positive | \|price change\| p90 |
   |---|---|---|
   | 1m | 53.5% | 0.00075 |
   | 5m | 56.4% | 0.00161 |
   | 15m | 58.1% | 0.00266 |
   | 30m | 59.3% | 0.00376 |

**Fix:** `OI_LOOKBACK = 30 minutes`, shared by both legs (`live_loop`
passes the matching `prior_as_of`, and the detector slices its price
window to the same span), and `_PRICE_SCALE = 0.0038` — the measured p90
of that window, the same `p90 -> tanh(1)` convention §16a established and
`relative_strength` already follows.

### 18c. Checked for edge BEFORE enabling it

Using the forward-outcome loop built earlier the same day (§17d) — which
is the point of having built it. Scoring every minute of both live
sessions under the fixed design and resolving the underlying's next 90
minutes:

| band | n | avg forward move | correct direction |
|---|---|---|---|
| all | 10,277 | +0.017% | **47.0%** |
| \|score\| >= 0.5 | 1,938 | +0.115% | **52.3%** |
| \|score\| >= 0.9 | 295 | +0.405% | **62.0%** |
| OI buildup (conviction 1.0) | 6,710 | +0.035% | 48.8% |
| OI unwind (conviction 0.5) | 3,567 | -0.018% | 43.7% |

**Monotone in score — the opposite of the confluence confidence §17d
condemned.** The buildup/unwind split is also correctly signed, so the
1.0-vs-0.5 conviction weighting the module docstring asserts is real
rather than decorative.

Same caveat as §17d and it is not a small one: **two sessions**, and
minute-spaced rows share nearly all of their forward window, so the 295
rows in the top band are a handful of independent episodes, not 295
observations. This is enough to justify enabling a dead detector. It is
not enough to build position sizing on.

### 18d. Live-verified, and what it changes

Smoke-tested against real recorded data at three instants across
2026-08-12 for TCS/INFY/RELIANCE: **`detectors=4` on all nine**, against
the 3.00 that had been invariant for 20,399 signals. Scores span
-1.0000 to +0.8531 with a healthy spread — one saturation (TCS 11:00,
during the move that made trade 62 +106%), not a constant. The new family
visibly does real work: at 14:30 it moves TCS from a 3-family consensus
to no direction at all.

NSE engines restarted onto `b461465` at 18:37 IST against a closed
market. 500 tests, ruff/mypy clean.

**`min_confidence` will drift again, and this time it is expected rather
than discovered.** Confidence is the weighted mean of `|score|` over the
*agreeing* families, so a fourth family with modest scores (median
`|score|` ~0.15-0.30) both establishes direction more often and pulls the
mean down. §17c's freshly re-sited 0.60/0.61 are therefore measured
against a three-family distribution that no longer exists. **Re-measure
both after 2026-08-13's close** — the same weekly cadence the config
comments already commit to. One session of mis-sited gates on a paper
book is the acceptable, known cost of getting a real distribution;
§17c is the proof that predicting it offline does not work.

### 18e. The US hazard is now closed, by halting rather than by tuning

Both US segments were **breaker-TRIPPED** this evening (user-approved),
`MANUAL_HALT`, on §15i's standing recommendation — LSE publishes no order
book on any of 541,234 recorded quotes, so every US fill is modelled
against an assumption with nothing to calibrate it. That also disposes of
§16f/§17g's restart hazard as a side effect: a tripped breaker refuses
entries regardless of which scorer the process loads, so the US engines
coming up on new code with old-distribution gates can no longer produce a
trade. The hazard is closed by the halt, not by the recalibration that
was never done.

`kairodex-ingest-{nse,us}` were also restarted, so §14e-bis's feed_health
liveness fix is finally running rather than merely committed.

### 18f. "Is everything on live?" — it wasn't, and the API had been lying for six days

Asked plainly at the end of the session. Checked instead of asserted, by
reading every unit's `ExecMainStartTimestamp` against the commits that
touch what it imports. Three of nine units were not on current code, and
one of them mattered.

**`kairodex-api` had been running since 2026-08-06 23:56** — through
§15's four exit/entry rules, §15g's intraday rule, §16's recalibration
and §17c's re-siting. A transitive-import check (walking `ast` over
`kairodex.api`'s import graph) found exactly one changed module it
actually depends on: `kairodex.config.segments`.

`GET /api/segments/nse_stock/risk` returned **HTTP 200** with a `config`
block containing no `min_confidence`, no `entry_warmup_minutes`, no
`entry_cutoff_minutes`, no `scratch_exit_*`, no `max_holding_sessions` —
the pre-§15 schema, served as current for two days. It did not error
because `SegmentRiskConfig` is `ConfigDict(frozen=True)` with no
`extra="forbid"`, so the old model silently drops YAML keys it has never
heard of, and `get_segment_config` is `@lru_cache`d so the process never
re-read the file. Restarted; the endpoint now reports `min_confidence`
0.60/0.61 and every P7 field.

**This is the same defect a fifth time** — §13d a fault reading green,
§14 a failure with no way to report failing, §15f a recovery reading red
forever, §16e a success that was partly a no-op, and now **a read API
confidently serving configuration that no process has used since
2026-08-10.** The invariant underneath all five: *the thing reporting the
state was not the thing holding the state.*

Root cause was documentation, not code: §3's deploy note named only the
recorder units, so every session since P6 has restarted `ingest` and
`engine` and never thought about `api`. §3 now carries the full unit ->
watched-paths table, a blunt restart-everything command, and the
start-time audit loop that found this.

**Deliberately not fixed:** `SegmentRiskConfig` still accepts unknown
YAML keys. Switching it to `extra="forbid"` would have turned this silent
drift into a loud startup failure — which is the right behaviour — but it
also means any future config field lands as a crash on every not-yet-
restarted process rather than a degraded one, and that is a call to make
deliberately rather than at the end of a session. Flagged here so it is
a decision rather than an oversight.

**Final live state, verified:**

| unit | code | note |
|---|---|---|
| `engine-nse_stock` / `engine-nse_index` | `b461465` (18:37 IST) | FLOW live, gates 0.60/0.61, ARMED |
| `engine-us_stock` / `engine-us_index` | pre-`0643e72` | deliberately stale — **breaker TRIPPED**, cannot trade |
| `ingest-nse` / `ingest-us` | current (18:15 IST) | §14e-bis feed_health fix running |
| `api` | current (18:5x IST) | config block correct again |
| `frontend` | 08-07 | no `frontend/` commits since; current by inspection |
| `jobs` | current | untouched by any of today's work |

---

## 19. P7-B — exits set from outcomes, and the scorer condemned (2026-08-14)

Prompted by "analyze why US takes no trades" and "what should we do to
harden both NSE segments." Both answers turned out to be the opposite of
what the symptoms suggested.

### 19a. US: nothing is broken, the halt is doing its job

Both segments read `breaker_status = TRIPPED`, `MANUAL_HALT`, since
§18e. On 08-13 that produced 1,072 `BREAKER_TRIPPED` rejections on
`us_stock`. There is no bug to fix on the entry path.

The halt's *reason* re-verified today rather than recalled — §15i's
numbers have not moved:

| writer | rows / 24h | bid | ask | bid_sz | ltp |
|---|---|---|---|---|---|
| LSE REST chain | 1,323,891 | **0** | **0** | **0** | all |
| LSE WS stream | 1,310,240 | **0** | **0** | **0** | all |
| Upstox NSE (same window) | 7,528,971 | 7,528,913 | 7,528,913 | 7,528,970 | all |

The `volume` double-meaning is also unchanged (REST median 11 =
cumulative daily; WS median 3 = that print), and `synthesize_quote`
still divides by it. **The fix is in the data layer, not the strategy**,
and it is the precondition for unhalting.

**New, and not previously recorded: `us_index`'s gate is unreachable.**
`min_confidence: 0.20` against 9,137 signals whose maximum confidence
ever recorded is **0.2317** — 161 rows (1.8%) clear it, 3 trades in the
segment's entire history. The gate sits near p98 of its own
distribution. §17g predicted a restart would leave US gates "wide open";
for `us_index` the new scorer moved the distribution *down* and the gate
is now nearly closed. Left alone (the segment is halted), flagged for
whenever US is next touched.

### 19b. The scorer is anti-predictive, confirmed out-of-sample

§17d measured this on 19,729 `nse_stock` signals. It now reproduces on
the independent 08-13 session (n=1,019, backfilled this session — 08-13
was entirely unscored, the whole first session with FLOW live):

| conf band | n | target hit | avg return (ATR) |
|---|---|---|---|
| 0.20-0.40 | 437 | 30.2% | -0.094 |
| 0.40-0.60 | 387 | 30.2% | -0.093 |
| 0.60-0.80 | 169 | **23.1%** | -0.308 |
| >= 0.80 | 7 | **14.3%** | -0.571 |

**And it is not the aggregation — each detector is individually
inverted**, joining stored per-detector `evidence` against
`forward_outcome` (baseline 33.3%):

| detector | \|s\|<0.2 | \|s\|>=0.2 | \|s\|>=0.5 | \|s\|>=0.9 |
|---|---|---|---|---|
| `relative_strength` | 33.4% | 31.6% | 30.4% | **22.8%** |
| `trend_structure` | 31.9% | 31.3% | 30.0% | **24.5%** |
| `iv_skew_sentiment` | 32.3% | 30.4% | 30.5% | 31.5% |

`relative_strength` sits *exactly* on the no-edge baseline when it has
no opinion and falls to 22.8% when most certain. `iv_skew_sentiment` is
flat everywhere — no information at any score level.

### 19c. FLOW did not reproduce — §18c's metric was the problem

§18c enabled `oi_price_flow` after measuring it monotone positive
(47.0% -> 62.0% "correct direction"). On the first full session with it
live, scored the way every other detector is scored:

| \|flow score\| | n | target hit | avg return (ATR) |
|---|---|---|---|
| < 0.2 | 120 | 28.3% | -0.150 |
| >= 0.2 | 644 | 28.9% | -0.134 |
| >= 0.5 | 200 | 30.0% | -0.100 |
| >= 0.9 | 31 | 29.0% | -0.129 |

Flat, and below baseline everywhere. §18c measured *direction
correctness over 90 minutes*; everything else is judged on *target-hit
at 2-ATR with a 1-ATR stop*. Different metric, encouraging answer, no
reproduction.

**Rule, and it is the real lesson of this session: measure a candidate
on the same metric the incumbents are judged on, or the comparison is
not one.** One session is not enough to condemn FLOW; it is enough to
stop calling it the family to build on.

### 19d. Three changes shipped, from 24 closed trades

Every trade closed since §15j's reset, replayed through a model of the
real exit ladder. **The replay reproduces all 24 live exit reasons and
lands within 11% of the real book** (-3,002 vs -2,709 actual), which is
what makes it usable for comparing variants; it prices exits at recorded
LTP rather than through the fill model, hence the gap.

The book itself: **-Rs 2,709 over 24 trades**, 33% win rate, 1.58 payoff
ratio against the 39% win rate that ratio needs. Short of breakeven by
structure.

**1. Exit ladder — stop 30%->20%, rungs (0.5,1,2)R -> (2,)R, scratch
90->45 min.** These are ONE decision: `R` is the stop distance, so the
rungs move with the stop. Rung reach across the 24: only 8 ever touched
+15% (the old first rung), 3 touched +30%, 1 touched +60% — the ladder
halved the position on exactly the third of trades carrying the book
while losers exited full size.

Sweeney excursion analysis puts the stop where the populations actually
separate:

| measure | winners (8) median / worst | losers (16) median / worst |
|---|---|---|
| drawdown by 30 min | -0.8% / **-9.2%** | -7.0% / -26.6% |
| peak reached | +26.5% / +12.2% | +3.4% / -6.2% |

No winner drew down past -9.2% in its first 30 session-minutes. The 30%
stop cut only 2 of 16 losers.

Replay: **-3,002 -> +7,311**, and still **+4,026 better with both
dominant winners deleted** — which the naive "remove the ladder" variant
is not (that one is +8,388 headline, 87% from one trade, and fails
leave-one-out). All sixteen cells of the surrounding stop x scratch grid
beat the live config, so it is a plateau rather than a fit.

The scratch rule was measured before being touched: removing it entirely
gives -8,614 against -3,002, so it is worth **+Rs 5,612 as it stood**.
It fires late, not wrongly — its +8% threshold against a median MFE of
+8.0% was a coin flip.

**2. `iv_skew_sentiment` unwired** (§19b's flat row). Module and tests
untouched — the defect is the feature (§16c's heavy tail), not the
detector. Leaves 2-of-3 on `nse_stock`. On `nse_index` it is **2-of-2**:
`relative_strength` reads above the 0.20 agreement threshold on **0 of
30** recorded evaluations there, and `iv_skew` was voting on 29 of 30,
so that segment's confluence had been carried mostly by the detector
with no information. It will trade markedly less; intended.

**3. `min_confidence` re-sited once and reframed.** `nse_stock`
0.60 -> **0.51**, purely to hold the old 20.4% pass rate after (2)
shifted the distribution (p75 0.5609 -> 0.5115). Both YAMLs now describe
it as a **volume throttle, not a quality filter**, with §19b's evidence
inline. `nse_index` deliberately **not** re-sited: 30 rows cannot site a
gate, and 0.61 atop a 2-of-2 requirement is the conservative direction.

**The weekly p75 re-siting ritual is retired.** It was fitting a
threshold to a meter that points the wrong way.

### 19e. Intraday momentum tested, and rejected

The one literature-backed candidate for a replacement detector (Gao,
Han, Li & Zhou, *JFE* 2018 — first half-hour predicts last half-hour).
Tested on existing data **before** writing any detector, per §19c's
rule. Session-return-so-far in ATR units vs. the traded side:

| stance | \|mom\|<0.5 | >=0.5 | >=1.5 | >=3 ATR |
|---|---|---|---|---|
| WITH the session move | 35.0% | 31.0% | 30.9% | **29.8%** |
| AGAINST it | 34.4% | 33.6% | 33.6% | 31.3% |

Trading *with* the move is worse than *against* it at every band, and
both decay with extension. The apparently-promising low-extension cell
does not survive a per-session split — the only session with real n
reads **28.0% on n=268**, and the aggregate was propped up by three
sessions of n=39-63.

**No detector was built.** What survives is one consistent negative:
high extension is below baseline in all four sessions. That is a filter
candidate, not an edge source — and removing it returns the population
to ~33% baseline, which for an options *buyer* is still a loss after
theta and spread.

**The honest state after this session: no detector in this system has
demonstrated positive edge.** Exits and cost control are the only levers
currently backed by evidence.

### 19f. A dead detector now reports itself

§18a said "nothing in the system treats a registered detector having
never fired as an error. It should." `kairodex status` now prints
per-detector appearance counts from `signals.evidence` over 24h and
names any that never fired.

**Built twice, because the first version was wrong and running it
against real data is what showed it.** A count-based check reported both
US segments as "3/3 firing" while `oi_price_flow` was absent from all
6,737 of their signals — those engines are on pre-FLOW code, so they run
a different three: same cardinality, wrong set, reported clean. It now
compares *names*, held in `ReferenceStrategy.detector_names` and pinned
by a test that evaluates the real strategy and asserts the declared set
is exactly what fires.

`wired_detectors` is passed in by the CLI rather than imported, because
`api.routers.health` also calls `build_report` and the "API is glue"
contract forbids `kairodex.api -> kairodex.strategy -> kairodex.engine`.
Importing it put the whole engine behind a health endpoint;
import-linter caught it, which is the contract working.

### 19g. `trades.mfe`/`mae`/`r_multiple` are dead columns, deliberately

Flagged as a defect, then verified as not one: **nothing writes them and
nothing reads them.** `analytics.loader` derives MFE/MAE at query time
from `position_marks.unrealized` so an open trade's excursion is always
what monitoring actually saw; `r_multiple` is a `TradeRecord` property;
and the promotion gates read `mfe_mae_ratio` off Track A metrics, not
these columns. Documented on the model rather than dropped — reading
`trades` directly and finding them empty looks exactly like a bug, which
is how this got raised.

Also checked and **not** changed: §17f called `nse_index`'s
`max_concurrent: 2` arithmetically unreachable. That is BANKNIFTY-
specific (Rs 9,139/lot against a Rs 15,000 exposure cap); Nifty 50 at
~Rs 2,144/lot fits twice over. Nothing to fix.

### 19h. Status

503 tests (4 new), ruff/mypy/import-linter clean. The one failure
(`test_expiry_cache`) is the known date-dependent US/LSE one — verified
failing identically on the parent commit.

Deployed against a closed market, 00:19 IST. `engine-nse_stock`,
`engine-nse_index` and **`kairodex-api`** restarted — the API because
`config/segments/*.yaml` is `@lru_cache`d and §18f is exactly what
skipping it costs. Verified: `GET /api/segments/nse_stock/risk` returns
`min_confidence: 0.51`, `scratch_exit_after_minutes: 45`.

**Not yet observed live.** Everything above is measured on recorded
data; the first session under these rules is 2026-08-14. Watch two
things: whether `nse_index` goes silent under 2-of-2 (expected, not a
bug — a full silent week is the signal to re-measure), and whether the
2R rung is ever reached at all.

---

## 20. P7-C — the features were never stored (2026-08-14, evening)

§19h's two watch items both resolved on the first live session under the
new rules. `nse_index` went silent — **6 evaluations, 0 trades**, all
rejected `BELOW_MIN_CONFIDENCE`, which is the 2-of-2 requirement working,
not a bug (a full silent week is still the signal to re-measure). And the
2R rung **was** reached: trade 80, BHARTIARTL 1980 CE, +49.5% in ten
minutes.

The day closed **-Rs 2,729 over 10 nse_stock trades**, 2 winners. Nothing
in §19's ruleset misfired; the five 45-minute scratches capped their
losses between -Rs 13 and -Rs 547.

### 20a. The largest loss driver is not the strategy

Three risk knobs contradict each other, and have since 05 Aug:

| constraint | formula | permits (at Rs 48,907 equity) |
|---|---|---|
| risk budget | `equity × base_risk_pct ÷ stop_pct` = `× 0.08 ÷ 0.20` | Rs 19,563 — never binds |
| premium cap | `equity × max_premium_pct` = `× 0.35` | **Rs 17,117 — binds every trade** |
| exposure cap | `equity × exposure_cap_pct` = `× 0.40` | funds **1.14** positions |
| `max_concurrent` | config | 5 — **unreachable** |

Because `max_premium_pct` (0.35) is below the risk-budget notional
(0.40 × equity), *every* trade asks for 35% of capital, and the exposure
cap then allows 40% total. The first signal after warmup is funded in
full; everything after divides a Rs 2,446 remainder. 171
`EXPOSURE_CAP_EXCEEDED` rejections today, the second-largest reason.

Confirmed exactly: trade 76 got 56 lots × 25 × Rs 12.18 = **Rs 17,052**;
a 57th lot would have breached the Rs 17,117 cap.

**What it costs.** The day's best trade — the 2R winner above — was sized
at **1 lot, Rs 391**, because two earlier positions had eaten the budget.
It returned Rs 192. At trade 76's notional it was worth ~Rs 8,400.

Across all 34 trades since the 08-11 reset, notional spans **Rs 148 to
Rs 17,052 (115×)** on positions the risk model believes are one identical
unit, and `corr(notional, return) = -1.2%` — not inverted, *random*.
Flat-weighting the same trades at Rs 5,000 gives **-Rs 3,256** against
the **-Rs 5,438** booked: Rs 2,182 of pure sizing variance.

**Not yet fixed** — the proposed shape is per-trade notional =
`exposure_cap_pct × equity ÷ max_concurrent`, so the cap is respected by
construction rather than by a first-come race.

### 20b. Other mechanical findings, none yet fixed

- **The stop runs on a 3.5-minute grid, last.** `EVAL_INTERVAL` is 60s but
  a watchlist sweep takes ~2.5 min, and exits are evaluated *after* it.
  `position_marks` gaps measured 161-222s avg, 280s max. Trade 84 filled
  at **-29.0%** on a -20% stop. Moving the exit pass ahead of the entry
  sweep is the highest value per line available.
- **`trail_pct` is still 0.30 while the stop moved to 0.20.** §19d moved
  the stop and the R-rungs together but the trail lives in
  `engine/monitor.py` and was missed. Above +14.3% MFE the trail binds
  *wider* than the initial risk.
- **The selector has no spread filter.** Sub-Rs-5 legs cost 4.39%
  round-trip and lost Rs 2,619. ITC 275 P: 9.53% round-trip against a
  +3.2% best excursion — unwinnable at any moment of its life.
- **Re-entry cooldown (30 min) is shorter than the scratch window (45).**
  BHARTIARTL re-entered the identical leg 32 minutes after stopping out;
  -Rs 3,430 on one underlying in one session.

### 20c. Three detectors, one signal — the mechanism behind §19b

`corr(trend_structure, oi_price_flow) = **0.813**`, same sign on **93.3%**
of 2,408 evaluations. The code explains it: `trend_structure` is
`tanh(EMA fast-slow spread)`; `oi_price_flow`'s *direction* is
`tanh(30-min price change / 0.0038)` — OI only scales magnitude. They are
the same measurement under two family labels, so `min_families: 2` is
satisfied automatically. `relative_strength` is the only weakly
independent input (r = 0.21 / 0.25).

So §19b is better stated as: there is **one** signal here — short-horizon
extension — and confluence multiplies confidence in it. Adding a fourth
momentum detector cannot help.

**New, and it changes the framing:** direction is *not* inverted.
Mirroring every signal gives 8.1% clean target hits against 31.6% as
signalled. **Direction is weakly right; confidence is backwards.** That
is the meta-labelling setup, not a reason to flip anything.

### 20d. The hurdle: what the trade costs, in ATR

All signal research here is in ATR of the underlying; the exit ladder is
in % of premium. Nobody had converted between them. Median nse_stock ATR
is **0.0689% of price**. Measured per quote over 778k liquid chain rows
(OI > 500k), charging theta over the 121-minute median hold:

| \|delta\| | spread | theta | **total hurdle** |
|---|---|---|---|
| 0.15-0.25 | 0.727 | 1.018 | 1.843 ATR |
| 0.25-0.35 | 0.604 | 0.797 | 1.486 |
| 0.35-0.45 | 0.554 | 0.635 | **1.276** ← was |
| 0.45-0.55 | 0.512 | 0.536 | **1.125** ← now |
| 0.55-0.65 | 0.570 | 0.440 | 1.068 (aggregate min) |
| 0.75-1.00 | 1.786 | 0.166 | 1.947 |

Against that, the **median signal's `mfe_atr` is 0.95** — its single best
moment. **Only 42.7% of signals ever travel far enough to cover the
option bought to express them** (43.0/43.3/43.0/42.7/39.6/39.4 per
session — stable, because it is cost arithmetic, not an edge claim).

A better direction model does not begin paying until it clears that.

### 20e. Shipped: A, B — and what C is still doing

**A. The engine now stores its features.** `orchestrator` called
`features.registry.compute_all` and discarded the result: `feature_vectors`
held **2 rows** (both 05 Aug) and `signals.feature_vector_id` was NULL on
all **79,209** rows. `features.store.compute_and_store` had existed since
P2 for exactly this, documented "(once wired in)", with zero callers.

Wired in *before* scoring, so non-signals and rejections are captured too
(§11: "the rejections are training data"). `write_feature_vector` now
returns the row id via `RETURNING` — on the conflict path too, so a
replayed tick links to the same row.

Live-verified the same evening against the open US market: after restart
the `us_stock` engine wrote 18 vectors (= watchlist size) on its first
tick and **18 of 18 subsequent signals linked, 100%**, while every one was
rejected at `BREAKER_TRIPPED` — which is the point.

**Immediately paid for itself:** the `quality` column resolved "18
features" to **15**. Three are `MISSING` on every row and are dead in the
live engine too — `iv_rank`/`iv_percentile` need an IV history nothing
supplies (and `option_quotes.iv` is NULL on all 64.8M rows while the
vendor's `vendor_iv` is 84.9% populated and unread), and
`opening_range_position` needs `session_open_ts`, which `build_context`
leaves "for the caller to set" and **no caller anywhere sets**. Same
defect shape as §18a's `prior_as_of` and `index_bars`. Not fixed here.

**B. `_DEFAULT_TARGET_DELTA` 0.40 -> 0.50**, per §20d's table. The
0.45-0.55 band beat 0.35-0.45 in **all five** sessions individually.
Deliberately stops short of the 0.55-0.65 aggregate minimum: that basin
is flat (0.06 ATR) and deeper ITM spends gamma — the convexity §19d's
ladder argument rests on — plus premium per lot against `max_premium_pct`.
Moves the payable share **42.7% -> 46.0%**, improving in all 6 sessions.
Lowers the cost of being right; does not make the system right.

Selector behaviour tests now pin `target_delta=0.40` explicitly instead of
inheriting the constant, and `test_target_delta_constant.py` pins 0.50 to
its evidence — precisely because the trail/stop pair drifted in §19d with
nothing failing.

**C. `kairodex backtest backfill-features`** reconstructs feature vectors
for signals scored before A existed, so the 20,729 existing
`forward_outcome` labels have a design matrix. `build_context` is already
a real point-in-time read; the correctness requirement is reproducing what
the engine saw, and both caller-supplied inputs are the ones that have
silently killed detectors here before — `prior_as_of = ts -
flow.OI_LOOKBACK` and the separate `index_bars` injection. Both pinned by
tests that compare against `live_loop`'s own call site.

**Running, not finished.** ~43 signals/min against 23,600, so several
hours. Idempotent and resumable — safe to re-run, it skips what is linked.

### 20f. Rejected while testing — do not re-propose without new evidence

**A hard 45-minute holding cap.** The theta arithmetic argues for it and
replay over the 34-trade book reads **+Rs 1,593** — but trade 84 alone
contributes +Rs 3,061, so deleting one trade flips the sign. Same
leave-one-out failure §19d used to reject "remove the ladder". It also
truncates the winners that carry the book (ADANIENT peaked at 275
minutes, BANKNIFTY 302, INFY 305), and most of its apparent gain comes
from cutting losers before their stop — a stop-and-cadence fix, not a cap.

Also tested and rejected on the §19e per-session split, all on the
incumbents' own metric (20,729 resolved signals, 33.3% baseline):

| hypothesis | aggregate | per session | verdict |
|---|---|---|---|
| invert the signal | 8.1% vs 31.6% | — | rejected outright |
| ATR regime 0.15-0.20% | 42.9%, +0.286 ATR (n=434) | 64.4 / 41.8 / 52.4 / **2.3** / 35.3 / 28.6 | noise |
| fade cross-sectional breadth | 39.8%, +0.193 (n=626) | 45.0 / 54.9 / 23.6 / **13.1** / 30.2 | one session carried it |
| time of day 09:45-10:59 | 28.4% vs 33.3% | below baseline in **5 of 6** | survives, as a filter |

Three of four died on the split. That is the base rate here, and it is the
reason nothing in §20e claims edge.

### 20g. Status

512 tests (9 new), ruff/mypy/import-linter clean, locally and on the VM.
All four engine units restarted 19:39 IST on `2e934e4`. `kairodex-api` not
restarted — nothing it reads changed.

`docs/reports/` is now in `.gitignore`. It had been kept untracked by
hand; a `git add -A` this session nearly committed three reports carrying
real P&L, positions and equity to GitHub.

**Next, and gated:** the meta-labelling model (§20c's framing) trained on
the substrate A and C produce, measured on P4's existing purged/embargoed
walk-forward before anything is wired — plus a real event calendar, since
`event_blackout_gate` unconditionally returns `True` and the system buys
11-DTE options into earnings blind.

### 20h. The five measured fixes, shipped (2026-08-14, 20:19 IST)

All measured off real trades this week. None is a strategy view and none
creates edge — they stop a bleed and remove noise larger than any effect
worth detecting.

| # | fix | where | evidence |
|---|---|---|---|
| 1 | exit sweep interleaved every 5 underlyings | `engine/live_loop.py` | stop checked every ~35s, was ~3.5 min |
| 2 | `trail_pct` derived from the position's own initial stop | `engine/monitor.py` | trail 0.30 vs stop 0.20 since §19d |
| 3 | reject legs wider than 2% of mid | `strategy/contract_selector.py` | sub-Rs-5 band: 4.39% RT, -Rs 2,619 |
| 4 | `reentry_cooldown_minutes` 30 -> 45 | `config/segments/*.yaml` | BHARTIARTL -Rs 3,430, re-entered at 32m09s |
| 5 | per-slot premium cap | `risk/sizing.py` | 115x notional dispersion, `corr(size,ret) = -1.2%` |

**On (1):** moving the sweep before the entry loop is *not* the fix and was
rejected — it changes the phase, not the period. Interleaving is what
shortens the gap. Not a second concurrent task either: that needs a second
session writing the same trade/equity rows, and those ordering bugs are
worse than the latency being fixed.

**On (2):** the trail is now *derived* from `initial_stop_price` rather than
configured separately, so the two cannot drift again. §19d moved the stop
and the rungs together on the reasoning that "R is the stop distance" and
the trail — the same decision — sat in another module as a bare literal.

**On (5):** verified numerically at the real 14 Aug closing equity. Every
position now sizes to Rs 3,450-3,700 against Rs 148-17,052 before —
dispersion 115x -> 1.07x — and five concurrent positions fit at 38.1% of
equity, inside the 40% cap. The day's 2R winner would have had **9 lots
instead of 1**. Expressed as a fourth cap in the same `min()` so
ARCHITECTURE.md §11's `risk_budget` formula still holds as written.

**Reverted while implementing, recorded so it is not re-proposed:** a
`qty_lots < 2` guard on the partial-exit rung. A "partial" of a 1-lot
position is the whole position, which is how the 2R winner closed with no
runner — but abstaining hands it to the stop instead, and
`test_stop_ratchet_does_not_pre_empt_a_partial_exit_on_the_same_tick` is a
regression built from real trade 4, a genuine 1-lot position that touched 1R
at 117.35 and stopped out at -Rs 271 after being +31%. Unmeasured judgement
against a documented real loss loses. Fix (5) is the actual cause of 1-lot
positions.

**A THIRD reason US cannot trade, found live within seconds of deploying.**
Both US segments now reject every candidate with
`NO_CONTRACT_INSIDE_SPREAD_LIMIT`, 100%, deterministically:
`synthetic_quote.SPREAD_PCT` is 0.04 and LSE publishes no order book at all
(§15i), so the whole US book is fabricated at an assumed 4% spread that can
never clear a 2% limit. Left as-is — a threshold measured on real NSE quotes
means nothing applied to a synthetic book, and refusing to buy an instrument
whose spread is an assumption is correct. But like §19a's unreachable
`us_index` gate it now sits outside its own distribution. **Do not unhalt US
expecting trades until `max_relative_spread` has a real per-segment value.**

Tests that inherited a constant now pin it explicitly (`trail_pct`,
`target_delta`, and the cooldown derived from `_CONFIG`) so the next knob
move fails loudly instead of silently changing what they assert. 518 tests,
ruff/mypy/import-linter clean, locally and on the VM. All four engines plus
`kairodex-api` restarted 20:19 IST on `ce54ad9` — the API because
`config/segments/*.yaml` changed, which is §18f exactly. Verified:
`GET /api/segments/nse_stock/risk` returns `reentry_cooldown_minutes: 45`.

**Not yet observed live.** NSE was shut when these deployed; the first
session under them is Monday 2026-08-17. Watch: whether five positions
actually open (max_concurrent has never been reachable before), and whether
any stop still fills more than a couple of points past its level.

## 21. P7-D — the meta-label model, measured (2026-08-15)

Steps A and C built the substrate; this is the first question asked of it.
Keep `ReferenceStrategy` deciding the SIDE — §20c established direction is
weakly *right* (inverting resolves 8.1% against 31.6% as-signalled) while
confidence is monotonically *backwards* — and learn a second model that
only decides whether to ACT. Binary, on labels that already existed.

`backtest/metalabel.py` + `kairodex backtest metalabel`. **Wired to
nothing.** No new dependency: logistic regression and AUC are ~30 lines of
numpy, and "is there any linear signal here at all" is the honest first
question. Purged/embargoed walk-forward reuses `validation.
walk_forward_splits` verbatim so the purge rule cannot drift from the
promotion gates'; standardisation and imputation statistics come from the
train fold only.

### 21a. The result

n=20,729, 15 live features, base rate 0.316, 3 usable folds after purging.

| feature set | mean AUC | folds > 0.5 | shuffles beating it | verdict |
|---|---|---|---|---|
| all 15 | 0.5262 | 3/3 | 0/30 (p=0.032) | outside the null |
| **used** — the 3 detectors read | **0.5053** | 2/3 | **7/30 (p=0.258)** | **noise** |
| **discarded** — the other 12 | **0.5268** | 3/3 | **0/30 (p=0.032)** | outside the null |

**The three features the strategy actually reads contribute nothing.** The
discarded twelve alone (0.5268) match all fifteen together (0.5262), and
the used three cannot be told apart from shuffled labels. That is §20c's
"three detectors, one signal" conclusion reproduced by an independent
method, and it is the first concrete payoff from persisting features.

A **label-shuffle null** is what makes any of this interpretable — without
it 0.526 cannot be distinguished from a biased harness. Thirty shuffles
preserve overlap, fold boundaries, class balance and imputation while
destroying the feature-label relationship; the null centres on 0.4971
(so the harness is unbiased) with a maximum of 0.5077.

### 21b. Do not deploy, and why

The signal is real but tiny. AUC 0.527 against 0.500. `lift@10%` — the
metric that actually matters if this gated entries — reads 1.22 / 0.95 /
1.01, i.e. below the base rate in one of three folds. Three folds over six
sessions, and p=0.032 is the resolution floor at 30 permutations, not a
strong number. Effective n is far below 20,729 because neighbouring
signals overlap.

### 21c. The one conditional that survives a per-session split

`vwap_position` carries the largest coefficient, and it is the first thing
tested this week to beat its own session's baseline in EVERY session:

| session | `vwap < -1` | that session's baseline | edge |
|---|---|---|---|
| 08-06 | 35.7 | 32.0 | +3.7 |
| 08-07 | 32.4 | 32.2 | +0.2 |
| 08-10 | 36.0 | 31.3 | +4.7 |
| 08-11 | 35.5 | 32.4 | +3.1 |
| 08-12 | 31.3 | 29.1 | +2.2 |
| 08-13 | 29.8 | 28.9 | +0.9 |

Six of six, mean about +2.5 points. `vwap > 1` is erratic by contrast
(-9.8 to +5.2), so the direction is **mean-reversion — long when price is
stretched BELOW VWAP** — consistent with §19e (against-the-move beat
with-the-move) and with confidence being an extension meter.

**It is still not an edge.** ~34% against the 33.3% breakeven that a
1-ATR-stop / 2-ATR-target payoff needs, before §20d's ~1.13 ATR instrument
hurdle. A filter candidate, exactly like the 09:45-10:59 window — not
something to build a strategy on.

### 21d. What this changes

The honest state is unchanged in its headline (§19: no detector has
demonstrated positive edge) but sharper in its direction:

- Stop re-weighting the three current detectors. They are one correlated
  momentum signal and the meta-model cannot separate them from noise.
- `vwap_position` and `volume_profile_poc_distance` are the candidates
  worth a detector, tested per §19c on the incumbents' own metric — which
  §21c has now started.
- More sessions matter more than a better model. Six sessions and three
  folds cannot support a stronger claim than "outside the null", and a
  gradient-booster on this much data would fit noise faster, not find more.

### 21e. Backfill fidelity — measured, and a real limit on mixing sources

C finished 2026-08-15 05:26 IST: **nse_stock 23,649/23,649 and nse_index
1,572/1,572, both 100%**, `no_instrument=0 no_context=0`, and 26,163
`signals.feature_vector_id` values resolve to 26,163 rows with **0
dangling**. Backfilled vectors carry a byte-identical schema to the ones
the live engine writes (same 18 quality keys, same three `MISSING`).

Then an exact per-row seam test, which is worth recording because it
failed at first and the diagnosis matters. `signals.evidence` stores
`score = tanh(trend_state_strength / _SCALE)` as written by the live
engine at signal time, so inverting it must reproduce the backfilled
value. Solving for the scale the engine actually used, per session:

| session | implied `_SCALE` |
|---|---|
| 08-10 | **0.0457** |
| 08-11 | 0.001417 |
| 08-12 | 0.001455 |
| 08-13 | 0.001530 |
| 08-14 | 0.001573 |

08-10 reads the OLD 0.05 constant, before §16a recalibrated it to 0.0018
— so a single-scale inversion was always going to mismatch there, and
that half of the failure was the test's fault, not the backfill's.

**The other half is a genuine limitation.** Post-recalibration sessions
imply ~0.0015 against the actual 0.0018 — about 15% low — drifting
*closer* to 0.0018 the more recent the session. That is the signature of
data revision: `build_context` is point-in-time with respect to `ts`, but
it reads `underlying_bars` **as they exist now**, and 1-minute bars
ingested or corrected after a signal fired were not available to the
engine at the time. Older data has had longer to be revised, which is
exactly the observed gradient.

Consequences, stated plainly:

- **§21's result is unaffected.** That dataset was 100% backfilled, so it
  is internally consistent, and the offset is systematic rather than
  noisy — standardisation removes a uniform scale anyway.
- **Mixed training sets are the hazard.** From the first NSE session under
  step A, live-written vectors land in the same table as backfilled ones
  on a slightly different footing. A model trained across that boundary
  can learn the seam instead of the market. Until this is quantified per
  feature, **train on one source or the other, not both** — and prefer
  live-written once enough of it exists.
- `relative_strength_vs_index` reconstructs at p90 = 0.0272 against §16's
  independently measured 0.022, so the divergence is not uniform across
  features and deserves a per-feature measurement before any mixed run.

## 22. Attacking the hurdle instead of the signal (2026-08-15)

§20d established the binding constraint: the option costs ~1.1 ATR and the
median signal delivers 0.95, so a better entry model earns nothing until it
clears that. `target_delta` was one constant on that equation and measuring
it moved the hurdle 1.276 -> 1.125 in an afternoon. Two more constants sit
on the same equation and had never been measured.

### 22a. DTE — hypothesis rejected, no change warranted

Theta is over half the hurdle and scales roughly as 1/sqrt(T), so a longer
tenor should cut it. Measured over liquid nse_stock quotes with the delta
band held at 0.45-0.55 and the hold held at 121 minutes:

| DTE band | spread ATR | theta ATR | **hurdle ATR** |
|---|---|---|---|
| 8-14 (what the selector picks) | 0.434 | 0.595 | **1.064** |
| 15-25 | 0.477 | 0.555 | **1.056** |
| 46+ | 1.332 | 0.315 | **1.646** |

Theta does halve as predicted (0.595 -> 0.315). But relative spread nearly
triples (0.92% -> 1.47%) because the far month is thin, and the net hurdle
is **55% worse**. NSE stock monthlies leave no 26-45 DTE window to exploit,
so there is no middle ground. **`_select_across_expiries`' "nearest working
expiry" is already the optimum.** No code change.

### 22b. Per-underlying hurdle — real, stable, and it varies 2.1x

The hurdle in ATR units is `cost% / ATR%`, and ATR% is a property of the
NAME. So the same option-buying strategy faces a materially different bar
depending on what it trades — which the watchlist has never been filtered on.

Measured per name (cost at 0.40-0.60 delta, OI > 200k; ATR from that name's
own resolved signals):

| | hurdle | clears | target hit | | | hurdle | clears | target hit |
|---|---|---|---|---|---|---|---|---|
| BHARTIARTL | 0.94 | 56.0% | 36.0% | | HINDUNILVR | 1.08 | 41.0% | 25.6% |
| MARUTI | 1.10 | 53.6% | 36.4% | | ONGC | 1.70 | 38.6% | 33.6% |
| RELIANCE | 0.83 | 52.7% | 33.2% | | ITC | 1.71 | 38.6% | 35.5% |
| SUNPHARMA | 1.01 | 52.2% | 36.2% | | WIPRO | 1.77 | 36.3% | 33.5% |
| SBIN | 0.87 | 51.6% | 30.1% | | HDFCBANK | 1.45 | 35.2% | 26.5% |
| ADANIENT | 1.54 | 43.2% | 38.2% | | KOTAKBANK | 1.42 | 33.0% | 24.2% |

`clears` is the share of that name's signals whose best moment exceeded its
OWN hurdle — deliberately distribution-aware rather than a median
comparison, because a median cut repeats §20f's leave-one-out mistake.
**ADANIENT proves the point**: second-worst hurdle (1.54) but mid-pack on
clears (43.2%), because its tail is the fattest in the watchlist (38.2% of
signals reach 2 ATR). A median-based filter would have dropped the name
that produced 14 Aug's single biggest winner.

The ranking validates externally against the real book: BHARTIARTL 1st and
produced the best trade of 08-14; ITC 17th and WIPRO 18th, both traded and
both lost; KOTAKBANK last and has never been traded at all.

**The COST half is stable** — per session the hurdle barely moves
(ADANIENT 1.53-1.54, ONGC 1.66-1.73, RELIANCE 0.79-0.89), which is expected
of a structural quantity and is why this is a better lever than signal
hunting.

### 22c. What this does NOT yet support

**A watchlist cut.** `clears_pct` decomposed per session is too thin to
rank the middle: ~30 signals per name per session, and it shows —
RELIANCE reads 100 in one session, WIPRO swings 10 -> 47, HDFCBANK 33 ->
67. Only the extremes survive: **KOTAKBANK never exceeds 42** (36/42/34/
34/33, worst or near-worst every session, and worst on target hit at
24.2%), and **BHARTIARTL never drops below 47**.

So: record the hurdle, do not re-site the watchlist on six sessions. The
cost ranking is trustworthy today; the outcome half needs more sessions.
Revisit once live-written features have accumulated (§21e — and note that
mixing them with backfilled rows is its own hazard).
