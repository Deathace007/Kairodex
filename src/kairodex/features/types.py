"""Shared types for the feature registry (ARCHITECTURE.md §9).

`FeatureContext` is plain data — no DB session, no I/O. Every registered
feature function is `FeatureContext -> float`, so its tests can build a
`FeatureContext` from synthetic `Bar`/`Tick` fixtures exactly like P1's
existing tests build synthetic ticks (see tests/unit/test_quality.py),
never a live DB. The one place that *does* touch the DB is `loader.py`,
which builds a `FeatureContext` for a given (instrument, as_of) — kept
deliberately separate so "is the math right" and "did we fetch the right
rows" are two different, independently testable questions.
"""

from __future__ import annotations

import datetime
import enum
from dataclasses import dataclass, field

from kairodex.core.enums import Segment
from kairodex.data.types import Bar, ChainSnapshot


class Tier(enum.IntEnum):
    """Matches the ingestion tier already on `option_quotes`/
    `watchlist_membership` (ARCHITECTURE.md §6) — a feature's tier is the
    tier of data it depends on, so T2-tier depth-derived features don't
    silently get evaluated somewhere only T1 REST-poll data is flowing."""

    T0 = 0
    T1 = 1
    T2 = 2


class Fidelity(enum.StrEnum):
    """ARCHITECTURE.md §9: without this, someone eventually reads a proxy
    as ground truth. EXACT = derived from a true observed value (a real
    print, a real quoted price). PROXY = reconstructed from what's
    actually available (e.g. the tick rule on L1/L2 snapshots standing in
    for aggressor-tagged prints on NSE). ESTIMATE = a deliberately
    approximate model output (e.g. net gamma exposure's dealer-positioning
    sign convention) — plausible, not verifiable against a true value."""

    EXACT = "EXACT"
    PROXY = "PROXY"
    ESTIMATE = "ESTIMATE"


class Quality(enum.StrEnum):
    """Per-feature outcome stored in feature_vectors.quality (ARCHITECTURE.md
    §5.3) — distinct from Fidelity: Fidelity is a static property of a
    feature *definition* (how good can this ever be), Quality is a per-
    computation outcome (did this particular evaluation actually work)."""

    OK = "OK"  # computed fine; Fidelity (on the FeatureSpec) says how good
    STALE = "STALE"  # computed, but off data older than the feature tolerates
    MISSING = "MISSING"  # required input wasn't available; no value computed


@dataclass(frozen=True, slots=True)
class FeatureContext:
    """Everything a feature function might read, for one underlying as of
    one instant. Optional fields default empty/None rather than being
    split into a dozen narrower context types — one flat, over-inclusive
    struct is simpler than a context-per-feature-family, and every field
    here is cheap to leave unused when a given feature doesn't need it.

    `underlying_bars`/`index_bars` must be ascending by `ts`, already
    truncated to as-of `as_of` — the loader's job, not the caller's (a
    feature function must never itself decide "is this bar in the future,"
    that's exactly the lookahead bug point-in-time correctness exists to
    prevent).

    `chain`/`prior_chain` are `list[ChainSnapshot]`, not `list[Tick]` —
    one entry *per expiry* (reusing `ChainSnapshot` exactly as P1 already
    does: it's a `Tick` list plus the `expiry` those ticks share). A flat
    `list[Tick]` can't support a feature like term structure, which
    inherently needs to compare two different expiries' ATM IV — `Tick`
    itself carries no `expiry` field, deliberately (see its docstring),
    so multi-expiry data has to be grouped one level up instead of bolting
    an `expiry` field onto every tick.
    """

    as_of: datetime.datetime
    segment: Segment
    underlying_bars: list[Bar] = field(default_factory=list)
    index_bars: list[Bar] = field(default_factory=list)
    chain: list[ChainSnapshot] = field(default_factory=list)
    prior_chain: list[ChainSnapshot] = field(default_factory=list)
    iv_history: list[tuple[datetime.datetime, float]] = field(default_factory=list)
    session_open_ts: datetime.datetime | None = None

    @property
    def spot(self) -> float | None:
        """Last observed underlying price. Individual features may prefer
        a different definition (e.g. mid of the ATM straddle) — this is
        just the obvious fallback when nothing more specific applies."""
        if not self.underlying_bars:
            return None
        return float(self.underlying_bars[-1].close)
