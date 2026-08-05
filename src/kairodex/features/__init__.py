"""Feature registry (ARCHITECTURE.md §9). Importing this package populates
`kairodex.features.registry`'s registry as a side effect — every
`compute/*.py` module registers its features at import time, so importing
`kairodex.features` (or calling anything in `registry`/`store`/`loader`,
which all import this) is what makes `registry.all_specs()` non-empty.
"""

from __future__ import annotations

from kairodex.features.compute import (  # noqa: F401
    iv,
    options_positioning,
    price_action,
    relative,
    volatility,
)
