# Kairodex — Development Progress

**Last updated:** 2026-08-05
**Current phase:** P1 (The Recorder) is done pending the unattended
5-session check (§7). P2 (Pricing & features) is functionally complete
(§8). **P3 (Engine & paper execution) is functionally complete**: every
piece ARCHITECTURE.md §3's engine box names — risk gate chain, execution
simulator, position monitor, contract selector, orchestrator — is built,
tested, and deployed as 4 systemd-supervised shadow-mode engine
processes (one per segment) on the VM, live-verified end to end against
real NIFTY chain data through to an actual simulated fill with real
pricing and costs. See §9 for the full account, including a critical
bug (every signal would have been rejected, forever) caught by that live
pass and fixed before deployment. **Not yet measured**: P3's own exit
criterion (full lifecycle in shadow mode for 5 sessions) needs real
calendar time to elapse — the clock started at deployment, see §9d.
**Next phase:** P4 (Backtest & validation), once the P3 review pass (§9d) is clean.

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
- **A subagent-driven review pass**, requested explicitly this session, is the next immediate step — covering the full P3 diff (risk engine, execution simulator, position monitor, contract selector, orchestrator, live loop) before treating it as done.
- **`instrument_specs`' real lot sizes aren't wired in** — `orchestrator.py` uses a fixed 25 (NSE) / 100 (US) default per market, not the real per-underlying SCD-2 lot size P0 already stores. Flagged inline with a `ponytail:` comment at its one call site.
- **`Strategy.manage()` exists but isn't wired into the orchestrator** — `run_exit_tick` calls `evaluate_exits` directly rather than through `strategy.manage()`, since this reference strategy's exits don't need feature context and building one per open position per tick would be pure overhead right now. Revisit once a strategy actually wants feature-aware exits.
- **Real per-underlying correlation clustering** doesn't exist — `correlation_cluster_gate` uses same-underlying-only as a first-pass proxy (documented in its own docstring).
