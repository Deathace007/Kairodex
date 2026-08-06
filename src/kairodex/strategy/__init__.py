"""Detectors, confluence scorer, contract selector (ARCHITECTURE.md §10).

Found missing during P6: every other package under `src/kairodex/` has
an `__init__.py`; this one didn't, which worked fine for regular imports
(implicit Python 3 namespace packages) but broke `import-linter`'s
module-graph resolution — `lint-imports` has been raising an unhandled
`ValueError: Module 'kairodex.strategy' does not exist` (not "contract
failed," a hard crash before any contract could even be checked) since
whenever `kairodex.strategy` first appeared in a contract's
`source_modules`/`forbidden_modules` (P3, "Optuna stays out of the live
trading path"). Confirmed by reproducing on `199a8aa` (pre-P6, before
this file existed) — this predates P6 entirely and had been silently
never-enforcing that contract the whole time.
"""
