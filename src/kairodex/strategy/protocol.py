"""Strategy protocol (ARCHITECTURE.md §10) and a reference implementation
bundling this session's four detectors — one per confluence family, enough
to prove the framework fires (and correctly refuses to fire) end to end.

`manage`'s implementation here just delegates to
`kairodex.engine.monitor.evaluate_exits` — that function only needs a
`Position`, not feature context, so `ctx` goes unused by this particular
strategy. `kairodex.engine.orchestrator.run_exit_tick` currently calls
`evaluate_exits` directly rather than through `strategy.manage()` (one
fewer `FeatureContext` build per open position per tick, since this
reference strategy's exit logic doesn't need one) — wiring `manage()`
into the orchestrator properly is the natural next step once a strategy
actually wants feature-aware exits (e.g. tightening a trailing stop on
an IV regime shift).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from kairodex.engine.monitor import ExitDecision as MonitorExitDecision
from kairodex.engine.monitor import Position, evaluate_exits
from kairodex.strategy.detectors.flow import oi_price_flow_detector
from kairodex.strategy.detectors.relative_strength import relative_strength_detector
from kairodex.strategy.detectors.structure import trend_structure_detector
from kairodex.strategy.detectors.volatility import iv_skew_detector
from kairodex.strategy.types import Evidence, MarketContext

Detector = Callable[[MarketContext], Evidence | None]


class Strategy(Protocol):
    # Read-only property, not a plain attribute: a plain `id: str` in a
    # Protocol demands write access too, which a frozen dataclass field
    # (ReferenceStrategy.id) structurally can't offer.
    @property
    def id(self) -> str: ...

    def evaluate(self, ctx: MarketContext) -> list[Evidence]: ...
    def manage(self, pos: Position, ctx: MarketContext) -> MonitorExitDecision | None: ...


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

    def manage(self, pos: Position, ctx: MarketContext) -> MonitorExitDecision | None:
        """`ctx` is unused — see the module docstring. Delegates entirely
        to `evaluate_exits`'s default thresholds (stop-loss, trailing
        stop, profit target, R-multiple partials, time/event exit)."""
        return evaluate_exits(pos, ctx.feature_ctx.as_of)
