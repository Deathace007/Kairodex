"""Shared types for the strategy framework (ARCHITECTURE.md §10).

`MarketContext` is what `Strategy.evaluate`/`.manage` and every `Detector`
read — a thin wrapper around P2's `FeatureContext` plus the feature
values already computed from it, so detectors never call the registry
themselves (compute once per evaluation tick, per §9, applies here too:
every detector in a strategy shares the same computed values, not a
fresh `compute_all` call each).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from kairodex.features.types import FeatureContext


class DetectorFamily(enum.StrEnum):
    """ARCHITECTURE.md §10: "requiring >= N independent detector families
    (structure / flow / volatility / relative-strength) to agree." Exactly
    these four — the confluence requirement is meaningless with a fifth
    family that's really a restatement of one of these."""

    STRUCTURE = "structure"
    FLOW = "flow"
    VOLATILITY = "volatility"
    RELATIVE_STRENGTH = "relative_strength"


@dataclass(frozen=True, slots=True)
class Evidence:
    """One detector's observation. `score` is signed: positive = bullish,
    negative = bearish, magnitude = conviction, always in [-1, 1] — a
    detector that produces a value outside that range has a bug, not a
    stronger opinion. `weight` > 0, relative importance within its family
    (most launch detectors just use 1.0 — see each detector's docstring
    for whether it varies)."""

    detector: str
    family: DetectorFamily
    score: float
    weight: float
    rationale: str

    def __post_init__(self) -> None:
        if not (-1.0 <= self.score <= 1.0):
            raise ValueError(f"score must be in [-1, 1], got {self.score}")
        if self.weight <= 0:
            raise ValueError(f"weight must be positive, got {self.weight}")


@dataclass(frozen=True, slots=True)
class MarketContext:
    feature_ctx: FeatureContext
    features: dict[str, float]
