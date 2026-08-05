import pytest

from kairodex.core.enums import Side
from kairodex.strategy.scorer import ConfluenceScorer
from kairodex.strategy.types import DetectorFamily, Evidence


def _ev(detector: str, family: DetectorFamily, score: float, weight: float = 1.0) -> Evidence:
    return Evidence(detector=detector, family=family, score=score, weight=weight, rationale="test")


def test_single_family_never_fires_however_strong():
    """ARCHITECTURE.md §10, literally: "Single-family agreement can never
    fire, whatever the score." — score=1.0 (maximum) from one family
    still must not produce a signal."""
    evidence = [_ev("d1", DetectorFamily.STRUCTURE, 1.0)]
    result = ConfluenceScorer().score(evidence)
    assert result.direction is None
    assert result.confidence == 0.0


def test_two_families_agreeing_bullish_fires_with_hand_computed_confidence():
    """STRUCTURE: two detectors, scores 0.8 and 0.6, weight 1.0 each ->
    family vote = (0.8+0.6)/2 = 0.7. RELATIVE_STRENGTH: one detector,
    score 0.5, weight 2.0 -> family vote = 0.5. Both > 0 -> BUY.
    confidence = weighted mean of |score| across all 3 agreeing pieces of
    evidence = (0.8*1 + 0.6*1 + 0.5*2) / (1+1+2) = 2.4/4 = 0.6."""
    evidence = [
        _ev("structure_a", DetectorFamily.STRUCTURE, 0.8),
        _ev("structure_b", DetectorFamily.STRUCTURE, 0.6),
        _ev("rel_strength", DetectorFamily.RELATIVE_STRENGTH, 0.5, weight=2.0),
    ]
    result = ConfluenceScorer(min_families=2).score(evidence)
    assert result.direction is Side.BUY
    assert result.confidence == pytest.approx(0.6, abs=1e-9)
    assert result.agreeing_families == {DetectorFamily.STRUCTURE, DetectorFamily.RELATIVE_STRENGTH}


def test_two_families_agreeing_bearish_fires_sell():
    evidence = [
        _ev("structure", DetectorFamily.STRUCTURE, -0.7),
        _ev("flow", DetectorFamily.FLOW, -0.4),
    ]
    result = ConfluenceScorer(min_families=2).score(evidence)
    assert result.direction is Side.SELL
    assert result.confidence == pytest.approx((0.7 + 0.4) / 2, abs=1e-9)


def test_conflicting_families_do_not_fire():
    """2 families bullish, 2 bearish, min_families=2: neither side has
    strictly more agreeing families than the other, so this is a genuine
    conflict, not a signal either direction."""
    evidence = [
        _ev("structure", DetectorFamily.STRUCTURE, 0.5),
        _ev("flow", DetectorFamily.FLOW, 0.5),
        _ev("volatility", DetectorFamily.VOLATILITY, -0.5),
        _ev("rel_strength", DetectorFamily.RELATIVE_STRENGTH, -0.5),
    ]
    result = ConfluenceScorer(min_families=2).score(evidence)
    assert result.direction is None


def test_more_bullish_than_bearish_families_still_wins():
    evidence = [
        _ev("structure", DetectorFamily.STRUCTURE, 0.5),
        _ev("flow", DetectorFamily.FLOW, 0.5),
        _ev("volatility", DetectorFamily.VOLATILITY, 0.5),
        _ev("rel_strength", DetectorFamily.RELATIVE_STRENGTH, -0.5),
    ]
    result = ConfluenceScorer(min_families=2).score(evidence)
    assert result.direction is Side.BUY
    assert len(result.agreeing_families) == 3


def test_agreement_threshold_filters_weak_votes():
    """A family voting only weakly bullish (below the threshold) doesn't
    count toward agreement, even though it's technically positive."""
    evidence = [
        _ev("structure", DetectorFamily.STRUCTURE, 0.9),
        _ev("flow", DetectorFamily.FLOW, 0.05),  # below threshold=0.1
    ]
    result = ConfluenceScorer(min_families=2, agreement_threshold=0.1).score(evidence)
    assert result.direction is None


def test_min_families_three_requires_three():
    evidence = [
        _ev("structure", DetectorFamily.STRUCTURE, 0.5),
        _ev("flow", DetectorFamily.FLOW, 0.5),
    ]
    result = ConfluenceScorer(min_families=3).score(evidence)
    assert result.direction is None

    evidence.append(_ev("volatility", DetectorFamily.VOLATILITY, 0.5))
    result = ConfluenceScorer(min_families=3).score(evidence)
    assert result.direction is Side.BUY


def test_empty_evidence_never_fires():
    result = ConfluenceScorer().score([])
    assert result.direction is None
    assert result.confidence == 0.0


def test_evidence_rejects_out_of_range_score():
    with pytest.raises(ValueError):
        Evidence(detector="x", family=DetectorFamily.FLOW, score=1.5, weight=1.0, rationale="x")


def test_evidence_rejects_nonpositive_weight():
    with pytest.raises(ValueError):
        Evidence(detector="x", family=DetectorFamily.FLOW, score=0.5, weight=0.0, rationale="x")


def test_rejects_bad_min_families():
    with pytest.raises(ValueError):
        ConfluenceScorer(min_families=0)


def test_rejects_bad_agreement_threshold():
    with pytest.raises(ValueError):
        ConfluenceScorer(agreement_threshold=1.0)
