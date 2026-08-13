"""Strategy protocol (ARCHITECTURE.md §10) and a reference implementation
bundling three of this codebase's four detectors — one per confluence
family except VOLATILITY, whose detector was unwired on 2026-08-14 after
it measured flat against forward outcomes at every score band (see
`_REFERENCE_DETECTORS` for the numbers and the route back).

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


# `iv_skew_detector` is deliberately NOT here, from 2026-08-14. The
# module and its tests are untouched — it is unwired, not deleted —
# because what is broken is the feature it reads, not the translation
# from feature to score.
#
# Measured over 19,729 nse_stock signals joined against their own
# `signals.forward_outcome` (PROGRESS.md §19), against a 33.3% no-edge
# baseline for the 1-ATR-stop/2-ATR-target resolver:
#
#   |score| < 0.2 : 32.3% target hit, -0.029 ATR
#   |score| >= 0.2: 30.4%            , -0.089
#   |score| >= 0.5: 30.5%            , -0.085
#   |score| >= 0.9: 31.5%            , -0.055
#
# Flat and slightly negative at every band — no information at any score
# level, while casting a full confluence vote. It reproduced flat on the
# independent 08-13 session too. §16c had already found the shape
# problem behind it (27.5% of readings below 0.01, 33.2% saturated above
# 0.99, from put IV - call IV with one leg occasionally garbage) and
# correctly called it a feature defect rather than a constant to retune.
# Fix `features/compute/iv.py::iv_skew` and re-wire this; until then a
# vote with no information is worse than no vote.
#
# Consequence worth stating: this leaves three families, so
# `min_families: 2` is 2-of-3 on nse_stock. On nse_index it is 2-of-2 —
# `relative_strength` measures an index against itself and reads above
# the 0.20 agreement threshold on 0 of 30 recorded evaluations there, so
# that segment now requires structure and flow to agree unanimously. It
# will trade markedly less; that is intended, not a regression. No
# `if segment ==` anywhere — the agreement floor does it, per §16a.
_REFERENCE_DETECTORS: tuple[Detector, ...] = (
    trend_structure_detector,
    oi_price_flow_detector,
    relative_strength_detector,
)
_REFERENCE_REQUIRED_FEATURES = frozenset(
    {"trend_state_strength", "oi_change", "relative_strength_vs_index"}
)
# The `Evidence.detector` label each wired detector emits. Declared here
# rather than derived because each label is a module-private constant in
# its own detector module, reachable only by actually calling the thing.
# `test_protocol.py::test_declared_detector_names_match_what_fires` runs
# them and asserts this set is exactly right, so it cannot drift.
#
# `kairodex status` compares this against the labels actually present in
# `signals.evidence` to report dead detectors. Counting alone is not
# enough and that was measured, not assumed: on 2026-08-14 both US
# segments reported "3/3 firing" while `oi_price_flow` was absent from
# all 6,737 of their signals — the engines are on pre-FLOW-fix code, so
# they run a different three, and a count-based check called that clean.
_REFERENCE_DETECTOR_NAMES = frozenset(
    {"trend_structure", "oi_price_flow", "relative_strength"}
)


@dataclass(frozen=True, slots=True)
class ReferenceStrategy:
    """One detector per family across structure/flow/relative-strength —
    still enough families to exercise the confluence requirement for real
    rather than trivially (a strategy with detectors from only one family
    could never demonstrate "single-family agreement can never fire"
    meaning anything). VOLATILITY is unwired, not missing — see
    `_REFERENCE_DETECTORS`. Not tuned or backtested; it exists to prove
    the pipeline wiring, not to trade well."""

    id: str = "reference_v1"
    detectors: tuple[Detector, ...] = field(default=_REFERENCE_DETECTORS)
    required_features: frozenset[str] = field(default=_REFERENCE_REQUIRED_FEATURES)
    detector_names: frozenset[str] = field(default=_REFERENCE_DETECTOR_NAMES)

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
