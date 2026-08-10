# Kairodex — Development Progress

**Last updated:** 2026-08-10
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
The strategy half waits on a clean fortnight of trades to calibrate
against. **Two live actions are still pending on the VM — see §15e.**

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

**Deploying a code change to the live recorder:** `git pull origin main` alone does *not*
pick up a running process's code — `systemctl restart kairodex-ingest-{nse,us,jobs}`
after every pull that touches `kairodex/data/` or `kairodex/jobs`.

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

### 15e. Two live actions still pending (blocked, need approval)

Neither is code; both need a hand on the VM:

1. **Repoint the four corrupted open trades** (5 MARUTI, 22 ONGC, 25 TCS,
   26 KOTAKBANK) from the abandoned duplicate row to the live-quoted one,
   and correct `max_holding_secs` from 259200 wall-clock seconds to the
   session-denominated equivalent. Until this runs, those four positions
   still have no working stop-loss. Script:
   transactional, printing the mapping before applying (the SQL is in
   this session's handover, not checked in — it is a one-off repair).
2. **`systemctl restart kairodex-engine-{nse_stock,nse_index,us_stock,us_index}`**
   — the VM is at `caac907` but the running processes are not.
