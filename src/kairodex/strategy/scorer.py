"""ConfluenceScorer (ARCHITECTURE.md §10): "requiring >= N independent
detector families to agree before a signal is emitted. Single-family
agreement can never fire, whatever the score." Implemented literally —
`min_families` defaults to 2, so nothing here has to trust any single
detector."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from kairodex.core.enums import Side
from kairodex.strategy.types import DetectorFamily, Evidence

_DEFAULT_MIN_FAMILIES = 2


@dataclass(frozen=True, slots=True)
class ConfluenceResult:
    direction: Side | None  # None = no signal — either not enough agreement, or a conflict
    confidence: float  # 0..1; 0.0 when direction is None
    agreeing_families: frozenset[DetectorFamily]
    evidence: tuple[Evidence, ...]  # every piece considered, not just the agreeing ones


class ConfluenceScorer:
    def __init__(
        self, *, min_families: int = _DEFAULT_MIN_FAMILIES, agreement_threshold: float = 0.0
    ) -> None:
        if min_families < 1:
            raise ValueError(f"min_families must be >= 1, got {min_families}")
        if not (0.0 <= agreement_threshold < 1.0):
            raise ValueError(f"agreement_threshold must be in [0, 1), got {agreement_threshold}")
        self.min_families = min_families
        self.agreement_threshold = agreement_threshold

    def score(self, evidence: list[Evidence]) -> ConfluenceResult:
        by_family: dict[DetectorFamily, list[Evidence]] = defaultdict(list)
        for e in evidence:
            by_family[e.family].append(e)

        # One weighted-average vote per family, not one vote per detector —
        # two detectors agreeing within the SAME family is not confluence,
        # it's correlation. This is what makes families independent units.
        family_vote: dict[DetectorFamily, float] = {}
        for family, items in by_family.items():
            total_weight = sum(e.weight for e in items)
            family_vote[family] = sum(e.score * e.weight for e in items) / total_weight

        bullish = frozenset(f for f, v in family_vote.items() if v > self.agreement_threshold)
        bearish = frozenset(f for f, v in family_vote.items() if v < -self.agreement_threshold)

        if len(bullish) >= self.min_families and len(bullish) > len(bearish):
            direction, agreeing = Side.BUY, bullish
        elif len(bearish) >= self.min_families and len(bearish) > len(bullish):
            direction, agreeing = Side.SELL, bearish
        else:
            return ConfluenceResult(
                direction=None,
                confidence=0.0,
                agreeing_families=frozenset(),
                evidence=tuple(evidence),
            )

        agreeing_evidence = [e for e in evidence if e.family in agreeing]
        total_weight = sum(e.weight for e in agreeing_evidence)
        confidence = sum(abs(e.score) * e.weight for e in agreeing_evidence) / total_weight
        return ConfluenceResult(
            direction=direction,
            confidence=confidence,
            agreeing_families=agreeing,
            evidence=tuple(evidence),
        )
