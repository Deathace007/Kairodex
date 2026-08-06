"""Performance metrics, breakdowns, and rollups over real (or backtest)
trades — ARCHITECTURE.md §15's `/performance`, `/analytics/breakdown`,
`/equity-curve` endpoints, and §14's export bundle. Same split as every
other package here: `types.py`/`performance.py`/`breakdowns.py`/
`rollups.py` are pure functions over plain dataclasses (DB-free,
unit-testable); `loader.py` is the one file that touches a session.
"""
