# 0004 — Drop Celery, MinIO, TA-Lib, PyFolio

## Status
Accepted (Celery: explicit user approval; the other three: no objection to
the proposal, decided by default per docs/SPEC_REVIEW.md §G)

## Context
SPEC.md's stack table names these four. Each has a lighter, already-present
alternative once the actual P0–P3 workload is examined concretely.

## Decision

**Celery → APScheduler + a Postgres jobs table.** The real workload is
three shapes: continuous ingestion (long-lived asyncio processes, which
Celery actively fights — it's built for discrete tasks, not permanent
streams), periodic rollups/exports (APScheduler, ~10 lines), and backtests
(a jobs table, or just the CLI while single-node). Celery's broker,
result backend, and worker-lifecycle management buy nothing at this scale.

**MinIO → local `./exports/` + chart references.** Its only named consumer
in SPEC.md is "screenshots or chart references if supported." Storing a
chart *reference* — `{instrument, timeframe, t0, t1, overlays[]}`,
re-rendered from TimescaleDB on demand — is strictly better than a
screenshot (queryable, never goes stale) and needs no object store at all.

**TA-Lib → Polars expressions.** A C dependency that reliably complicates
Docker builds, for roughly six indicators (ATR, EMA, RSI, ADX, VWAP,
Bollinger) that are ~80 lines of vectorized Polars and belong in the
feature pipeline anyway. SPEC.md itself says indicators must never be the
sole reason for a trade, so this surface is deliberately small.

**PyFolio → in-house metrics + QuantStats for offline tearsheets only.**
PyFolio has been unmaintained since 2020. The live dashboard needs
per-segment, per-regime, rolling metrics queryable from Postgres — pandas
HTML tearsheets don't fit that shape regardless of which library produces
them. QuantStats is kept for its actual fit: offline research tearsheets.

## Consequences
- Fewer moving parts to operate for a single-node deployment.
- Reversible: each of these is a bounded addition (a broker + workers, a
  storage adapter, a vectorized-indicator swap, a metrics library import)
  if a future requirement genuinely needs it — nothing here is a one-way
  door.
