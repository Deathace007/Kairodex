# Kairodex — Development Progress

**Last updated:** 2026-08-05
**Current phase:** P1 (The Recorder) — **both markets now live-verified.**
US/LSE: migrations applied, T0/T1 sync, `ingest run` confirmed writing real
option_quotes, **0.02% gap rate** (target <0.5%). US_INDEX (ADR 0007:
SPY/QQQ/DIA/IWM) fully live-verified. NSE/Upstox: live-verified
2026-08-05 during market hours — real bug found and fixed (WS silently
drops the whole subscription above ~2000-3000 keys, no error surfaced;
see §1). Post-fix: `subscribed_count` climbing past 1000+ real
instruments, **0.00% gap rate**, real per-tick timestamps confirmed.
**Next phase:** decide process supervision (§7), then P2 (Pricing & features).

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
