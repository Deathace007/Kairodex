import datetime

import pytest

import kairodex.features  # noqa: F401 — populates the registry as a side effect
from kairodex.core.enums import Segment
from kairodex.features import registry
from kairodex.features.types import FeatureContext, Fidelity


def test_all_18_launch_features_registered():
    """ARCHITECTURE.md §9 lists 15 concept bullets; a few (ATR/realized
    vol, IV rank/percentile, OI change & PCR) bundle two genuinely
    distinct numbers each — registered as separate entries rather than
    forced into one artificial blended scalar (see docs/PROGRESS.md P2
    §8). 15 concepts -> 18 registry entries."""
    names = {s.name for s in registry.all_specs()}
    expected = {
        "atr",
        "realized_vol",
        "volatility_regime",
        "trend_state_strength",
        "vwap_position",
        "opening_range_position",
        "volume_profile_poc_distance",
        "price_acceptance",
        "relative_strength_vs_index",
        "index_correlation",
        "iv_rank",
        "iv_percentile",
        "iv_skew",
        "term_structure",
        "oi_pcr",
        "oi_change",
        "net_gamma_exposure",
        "liquidity_score",
    }
    assert names == expected


def test_duplicate_registration_raises():
    with pytest.raises(ValueError, match="already registered"):
        registry.register(
            name="atr",
            inputs=[],
            tier=registry.Tier.T1,
            fidelity=Fidelity.EXACT,
            backtestable={},
            cost_ms=1,
        )(lambda ctx: 0.0)


def test_compute_all_reports_missing_for_a_raising_feature():
    """A feature that raises must not take down every other feature's
    computation for the same tick — ARCHITECTURE.md §7's "poisoned rows
    never silently dropped" applied to features."""

    @registry.register(
        name="_test_always_raises",
        inputs=[],
        tier=registry.Tier.T1,
        fidelity=Fidelity.EXACT,
        backtestable={"nse": True},
        cost_ms=1,
    )
    def _broken(ctx: FeatureContext) -> float:
        raise RuntimeError("boom")

    try:
        ctx = FeatureContext(as_of=datetime.datetime.now(datetime.UTC), segment=Segment.NSE_INDEX)
        values, quality = registry.compute_all(ctx)
        assert "_test_always_raises" not in values
        assert quality["_test_always_raises"] == "MISSING"
        # a real feature (atr, with empty bars) also reports MISSING
        # cleanly rather than raising — confirms the try/except isn't
        # masking every feature the same way regardless of cause
        assert quality["atr"] == "MISSING"
    finally:
        del registry._REGISTRY["_test_always_raises"]


def test_compute_all_returns_ok_for_computable_feature():
    ctx = FeatureContext(
        as_of=datetime.datetime.now(datetime.UTC),
        segment=Segment.NSE_INDEX,
        iv_history=[
            (datetime.datetime.now(datetime.UTC), 0.2),
            (datetime.datetime.now(datetime.UTC), 0.3),
        ],
    )
    values, quality = registry.compute_all(ctx)
    assert quality["iv_rank"] == "OK"
    assert "iv_rank" in values
