"""ARCHITECTURE.md §15 — a thin, read-mostly FastAPI layer. Writes are
limited to human control actions (promote / breaker / kill), all
audited (`audit_log`). import-linter enforces the boundary this package
name implies: `kairodex.api` may never import `kairodex.strategy`,
`kairodex.risk`, `kairodex.engine`, or `kairodex.backtest` — every
endpoint here is glue over `kairodex.store`/`kairodex.analytics`/
`kairodex.export`/`kairodex.config`/`kairodex.pricing`, never business
logic of its own. `POST /api/backtests` is the one place that boundary
bites: it can't call `kairodex.backtest` in-process, so it shells out to
the `kairodex` CLI instead (see `routers/backtests.py`).
"""
