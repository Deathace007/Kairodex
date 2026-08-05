"""Execution simulator (ARCHITECTURE.md §12). Exactly two `ExecutionPort`
implementations exist in this package — `SimulatedBroker`, `ShadowLogger`
— and neither talks to a real broker; there is no order-placement
credential anywhere in this codebase or its config schema. "Live trading
is not a flag — it's absent code" (§11).

No module-level `PAPER_ONLY` assertion here, deliberately: it was tried
and reverted (caught before shipping) — a check at import time makes
`DATABASE_URL` a transitive requirement just to import a pure function
from `kairodex.execution.fills`, breaking the DB-free unit-test
principle every other package in this codebase follows. It's also
redundant: `Settings.kairodex_paper_only`'s own field validator
(kairodex/config/settings.py) already makes it structurally impossible
to construct a `Settings` object with `KAIRODEX_PAPER_ONLY=false` at
all — every real entrypoint calls `get_settings()` long before it would
reach an `ExecutionPort`, so the guarantee already exists at the one
layer that actually needs it.
"""

from __future__ import annotations
