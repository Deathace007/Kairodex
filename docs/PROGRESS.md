# Kairodex — Development Progress

**Last updated:** 2026-08-04
**Current phase:** P1 (The Recorder) — verified against live infra for US
(LSE, market open at test time): migrations applied, T0/T1 sync, and
`ingest run` confirmed writing real option_quotes with `kairodex status`
showing a connected, streaming feed. NSE (Upstox) still needs a live pass
during market hours (9:15–15:30 IST) — see §7. **US_INDEX segment has a
real vendor-coverage gap**, see §1's last row and §4a.
**Next phase:** finish NSE live verification, then P2 (Pricing & features).

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
| **LSE carries no SPX/NDX/RUT options at all** — `us_index` watchlist shipped empty | Verified live 2026-08-04: `list_expiries()` for all three returns zero contracts against LSE's full 3,186-underlying catalog, no error, just not offered. Equity/ETF options only (SPY/QQQ/IWM, already in `us_stock`) | `config/watchlist.yaml`; **US_INDEX segment (SPEC.md's 4th segment) has no viable data source with the current vendor** — needs a call: different vendor, proxy via SPY/QQQ options, or drop the segment |
| **Per-tick SEQUENCE_GAP quality flag removed from the WS path** | Live-verified: a 2s expected-interval threshold flagged most of a healthy live book as "gapped," since individual option contracts (especially thin strikes) legitimately go minutes between prints — that's normal, not a feed problem. `feed_health.connected`/`last_message_at` is the real stream-liveness signal | `kairodex/data/recorder.py` — `flag_tick()` still checks STALE/CROSSED_BOOK/ZERO_VOLUME/OUTLIER per tick |

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

# P1: bring up the recorder for a market, in order
uv run kairodex ingest sync-instruments --market nse   # T0: full instrument master -> instruments
uv run kairodex ingest sync-watchlist   --market nse    # T1: seed watchlist_membership from config/watchlist.yaml
uv run kairodex ingest run              --market nse    # long-lived: T1 REST poll + WS stream (repeat for --market us)
uv run kairodex status                                  # per-provider connection state, last message age, gap rate
uv run kairodex jobs                                    # long-lived: annual Upstox token-expiry check

# Inspect the DB directly
docker exec kairodex-timescaledb-1 psql -U kairodex -d kairodex -c "SELECT count(*) FROM instruments;"
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
**LSE has no SPX/NDX/RUT options at all** (`us_index` ships empty, needs a
product decision), and the WS path's SEQUENCE_GAP flag used too tight a
per-contract threshold and was removed from that path.

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
- No compose services added for `ingest`/`jobs` — there's no `Dockerfile` and the established pattern (per this file's own command reference) runs `kairodex` processes as bare `uv run` commands on the VM, only DB/Redis are containerized. Process supervision (systemd/tmux/something else) is an open operational choice, not decided here.

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

---

## 7. Next up — finish live verification, then start P2

**NSE/Upstox still needs a live pass** during market hours (9:15–15:30 IST,
next window from this session's 2026-08-04 22:30 IST): `kairodex ingest run
--market nse`, watch `kairodex status`, and diff a few real WS messages
against `kairodex/data/upstox/feed.py`'s field mapping — the vendor's own
`.proto` should mean the shape is right, but *values* (are greeks/depth
actually populated the way `MarketFullFeed` implies) are still unconfirmed.
The LSE field-name mistake in §6 #2 is exactly the class of bug to look
for. Fix and log any surprises the same way #2 was handled.

**Needs a product decision, not more code:** the US_INDEX segment (§1) has
no viable data source with LSE as the vendor. Options: find/add a vendor
that carries SPX/NDX/RUT, treat SPY/QQQ/IWM 0DTE options as an explicit
proxy (different settlement/exercise characteristics — a real product
change, not equivalent), or drop US_INDEX from scope. `config/watchlist.yaml`
ships with `us_index: []` until this is decided.

**Exit criterion** (ARCHITECTURE.md §19, unchanged): 5 consecutive trading sessions recorded, < 0.5% gap rate on T1, clean restart mid-session with no data loss. `kairodex status`'s gap-rate line is the number to watch — now STALE-driven only (see §1/#8), so it should actually mean something.

Once NSE is verified and US_INDEX is decided, P2 (Pricing & features) is next: Black-76 + Bjerksund, IV solve, forward derivation, feature registry.
