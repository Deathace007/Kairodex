"""Concrete detectors (ARCHITECTURE.md §10), one per confluence family —
enough to prove the framework end to end, not the full backlog of ~40
market-analysis concepts (that's the feature registry's own launch-set
scoping, ARCHITECTURE.md §9, repeated here for the same reason).

Each is a thin translation from an already-computed P2 feature value into
a signed [-1, 1] `Evidence` score — no detector re-derives its own
number, they read `ctx.features[...]`. Scaling constants (what magnitude
of a feature counts as "strong") are first-pass estimates from standard,
documented technical-analysis conventions, not calibrated against real
historical data — that calibration is P4's job, once backtesting exists
to do it properly. Marked in each detector's own docstring.
"""
