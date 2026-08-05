"""Strategy protocol (ARCHITECTURE.md §10) and a reference implementation
bundling this session's four detectors — one per confluence family, enough
to prove the framework fires (and correctly refuses to fire) end to end.

`manage(self, pos: Position, ctx: MarketContext) -> ExitDecision | None`
from the spec's full Protocol is deliberately not implemented here —
`Position`/`ExitDecision` belong to the position monitor (§11/§12), not
built this session (see docs/PROGRESS.md §9's "not started" list). A
`Strategy` here is entry-side only: `evaluate` plus enough metadata for
an orchestrator to know what it needs.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from kairodex.strategy.detectors.flow import oi_price_flow_detector
from kairodex.strategy.detectors.relative_strength import relative_strength_detector
from kairodex.strategy.detectors.structure import trend_structure_detector
from kairodex.strategy.detectors.volatility import iv_skew_detector
from kairodex.strategy.types import Evidence, MarketContext

Detector = Callable[[MarketContext], Evidence | None]


class Strategy(Protocol):
    id: str

    def evaluate(self, ctx: MarketContext) -> list[Evidence]: ...


_REFERENCE_DETECTORS: tuple[Detector, ...] = (
    trend_structure_detector,
    oi_price_flow_detector,
    iv_skew_detector,
    relative_strength_detector,
)
_REFERENCE_REQUIRED_FEATURES = frozenset(
    {"trend_state_strength", "oi_change", "iv_skew", "relative_strength_vs_index"}
)


@dataclass(frozen=True, slots=True)
class ReferenceStrategy:
    """One detector per family (structure/flow/volatility/relative-strength)
    — exercises the confluence requirement for real rather than trivially
    (a strategy with detectors from only 1-2 families could never
    demonstrate "single-family agreement can never fire" meaning
    anything). Not tuned or backtested; it exists to prove the pipeline
    wiring, not to trade well."""

    id: str = "reference_v1"
    detectors: tuple[Detector, ...] = field(default=_REFERENCE_DETECTORS)
    required_features: frozenset[str] = field(default=_REFERENCE_REQUIRED_FEATURES)

    def evaluate(self, ctx: MarketContext) -> list[Evidence]:
        evidence = []
        for detector in self.detectors:
            result = detector(ctx)
            if result is not None:
                evidence.append(result)
        return evidence
