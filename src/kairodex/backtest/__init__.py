"""P4 — Backtest & validation (ARCHITECTURE.md §13): `ReplayClock` drives
the identical strategy/scorer over recorded underlying OHLCV. Track A
only — option economics are Track B (live shadow, already running via
P3's engine)."""
