# Kairodex — Development Progress

**Last updated:** 2026-08-04
**Current phase:** P0 (Foundations) — complete and verified against live infra.
**Next phase:** P1 (The Recorder) — not started.

> Update this file whenever a phase completes, a command/location changes,
> or a bug worth remembering gets fixed. This is the file a fresh session
> should read first — deeper reasoning lives in ARCHITECTURE.md,
> SPEC_REVIEW.md, and docs/adr/.

---

## 1. Where everything lives

| What | Where |
|---|---|
| **Local repo** (edit code here only) | `/Users/mohanborle/AI_ML/Kairodex` |
| **GitHub remote** | `git@personal:Deathace007/Kairodex.git` (branch `main`), via the `personal` SSH host alias — `~/.ssh/config`, key `~/.ssh/id_ed25519_personal` |
| **VM** (all Docker/DB/tests/ingestion run here) | E2E Networks, `ssh -i ~/.ssh/id_ed25519_personal root@164.52.206.92` |
| **VM repo clone** | `/opt/Kairodex` |
| **VM Docker Compose project** | `kairodex` → containers `kairodex-timescaledb-1`, `kairodex-redis-1` |
| **VM `.env`** | `/opt/Kairodex/.env`, mode 600 — transferred once via `scp -O` (VM's sshd has no SFTP subsystem configured; `-O` forces the legacy SCP protocol) |

**Workflow, always:** edit locally → `git commit` → `git push` → SSH to VM → `git pull` → run. Never run Docker, Postgres, or long-lived processes on the local laptop for this project (see `docs/adr/` and memory `infra_vm_workflow.md`).

**Also on the VM, in `/opt/`:** a separate, unrelated app `swingpro` (compose project name `infra`). Its containers/images were torn down on 2026-08-04 to reclaim disk (116GB of build cache was the bulk of it); its 8 volumes were deliberately left untouched. If it needs to come back, `docker compose up` from its own directory should still work off those volumes.

---

## 2. Quick command reference

```bash
# SSH to the VM
ssh -i ~/.ssh/id_ed25519_personal root@164.52.206.92

# On the VM: pull latest code and bring up infra
cd /opt/Kairodex && git pull origin main
source $HOME/.local/bin/env   # puts uv on PATH
docker compose up -d
docker compose ps             # both should show "healthy"

# Run migrations
uv run alembic upgrade head
uv run alembic current        # confirm at head

# Lint / type-check / test (same commands locally or on VM)
uv run ruff check .
uv run mypy src/kairodex/
uv run pytest -q

# The CLI (only real command so far)
uv run kairodex ingest pull-chain --market nse --underlying "NSE_INDEX|Nifty 50" --expiry YYYY-MM-DD
uv run kairodex ingest pull-chain --market us  --underlying AAPL --expiry YYYY-MM-DD

# Inspect the database directly
docker exec kairodex-timescaledb-1 psql -U kairodex -d kairodex -c "SELECT count(*) FROM instruments;"
```

**Finding a real NSE expiry date** (NIFTY expiries are Tuesdays, not obvious guesses):
```python
from kairodex.data.upstox.client import UpstoxClient
from kairodex.data.upstox.auth import AnalyticsToken
from kairodex.config import get_settings
# ... instantiate client, iterate client.instruments(), filter underlying_symbol == "NIFTY"
```

**Local-only commands** (no DB/Docker needed): `uv run ruff check .`, `uv run mypy src/kairodex/`, `uv run pytest -q` all work fine on the laptop since the current test suite has no DB dependency yet. Anything touching Postgres/Redis/live vendor calls: VM only.

---

## 3. Completed — P0 (Foundations)

Everything below is verified against live systems, not just "should work":

- **Repo scaffold**: `uv` project, `src/kairodex` layout, ruff + mypy (strict) + import-linter, all clean.
- **Docker Compose**: TimescaleDB (`timescale/timescaledb-ha:pg16.14-ts2.28.1-all`) + Redis. Healthy on the VM.
- **Schema** (Alembic migration `354672f0f639`): reference tables (`instruments`, `instrument_specs`, `trading_calendar`, `watchlist_membership`, `corporate_actions`, `fx_rates`) + market-data hypertables (`underlying_bars`, `option_quotes` with compression, `chain_snapshots`, `market_depth` with 30-day retention, `options_flow`). Applied and confirmed on the VM.
- **Upstox adapter** (`kairodex.data.upstox`): Analytics Token auth (long-lived, read-only — see ADR 0006), instrument master (verified against the real live file: 34,738 instruments, correct NSE_STOCK/NSE_INDEX segment classification), option chain, historical candles v3, rate limiting (50/s, 500/min, 2000/30min). **Live-tested**: pulled a real 226-contract NIFTY chain into Timescale.
- **LSE adapter** (`kairodex.data.lse`): thin async wrapper over the official `lse-data` PyPI client. **Live-tested**: pulled a real 126-contract AAPL chain into Timescale, after fixing two field-name bugs found during that test (see §5).
- **`MarketDataProvider` port**: shared contract both adapters implement (`kairodex/data/ports.py`).
- **Ingest/upsert layer** (`kairodex/data/ingest.py`): resolves instruments by natural key, writes `chain_snapshots` + `option_quotes`. Minimal by design — the real tiered recorder is P1.
- **CLI** (`kairodex ingest pull-chain`): proves the full path end to end.
- **6 ADRs** in `docs/adr/` recording every real architectural decision, including two corrections made *during* implementation (see §5).
- **Project renamed** `otp` → `kairodex` (mid-P0, user's choice — avoids collision with "one-time password").

## 4. Not done yet (explicitly out of P0 scope)

- No WebSocket/live streaming for either vendor (`subscribe()` raises `NotImplementedError` — P1).
- No tiering (T0/T1/T2), no quality flags, no feed-health monitoring, no quota-aware scheduling — all P1.
- No pricing module (Greeks/IV computed by us — P2).
- No engine, strategies, risk, execution simulator — P3.
- No backtest/walk-forward — P4.
- No API/dashboards — P6.
- Per-segment risk config (`config/segments/*.yaml`) doesn't exist yet — deferred to P3 when the risk engine consumes it (see `docs/ARCHITECTURE.md` §11 note).

## 5. Bugs found and fixed along the way (worth knowing so they aren't reintroduced)

1. **`.gitignore` swallowed real code.** The pattern `data/` (unanchored) matched `src/kairodex/data/` — the entire vendor-adapter package — not just the intended top-level runtime-artifacts directory. Fixed to `/data/`. Always check `git status`/`git check-ignore -v <path>` after adding a broad ignore pattern.
2. **Upstox auth was originally over-built.** Assumed daily-expiring OAuth (the "#1 operational risk" in `SPEC_REVIEW.md` §C1) before the user corrected: Upstox's **Analytics Token** is long-lived (1yr), read-only, dashboard-generated — no OAuth flow needed at all. See ADR 0006.
3. **LSE chain field names were guessed wrong.** Written from reading the vendor client's source (no test key available at the time) as `type`/`volume`; the real API uses `contract_type` (`"call"`/`"put"`) and `volume_today`. Caused a Postgres unique-constraint violation on first live test (colliding synthesized instrument identities). Fixed once real credentials made verification possible — lesson: anything flagged "BEST-EFFORT, unconfirmed" in a docstring needs a live check before it's trusted.
4. **Local `.venv` broke after the project folder was renamed** (`OptionTradingSystem` → `Kairodex`) — venv shebangs bake in absolute paths, so `mypy`/`pytest` failed with "bad interpreter" until `.venv` was deleted and recreated with `uv sync`. If local commands mysteriously fail after moving the repo, this is why.
5. **`scp` (SFTP protocol) fails on this VM** — no SFTP subsystem configured in sshd. Use `scp -O` (legacy SCP protocol) instead.

---

## 6. Next up — P1: The Recorder

Per `docs/ARCHITECTURE.md` §19, this is the highest-priority phase — every session that passes before it's live is market data that can never be recovered.

- Tiered ingestion (T0 universe / T1 watchlist / T2 focus) per §6 capacity plan
- WebSocket streaming for both vendors (wire up `subscribe()`)
- Quality flagging (staleness, crossed books, gaps) — `kairodex/data/quality.py`
- Feed health tracking (`feed_health` table, per §7)
- Upstox token pre-flight check (annual, not daily — just needs a scheduled reminder)
- Restart recovery (resume from `max(ts)` per instrument)
- A minimal status page (not the full dashboard — that's P6)

**Exit criterion:** 5 consecutive trading sessions recorded with < 0.5% gap rate on T1, and a clean restart mid-session with no data loss.
