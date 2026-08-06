"""Every value asserted here is copy-checked against ARCHITECTURE.md
§11's own table, not just "does it load"."""

import pydantic
import pytest

from kairodex.config.segments import get_segment_config
from kairodex.core.enums import Segment


@pytest.mark.parametrize(
    "segment,capital,currency,base_risk_pct,hard_ceiling_pct,max_premium_pct,max_concurrent",
    [
        (Segment.NSE_STOCK, 50000, "INR", 0.08, 0.35, 0.35, 5),
        (Segment.NSE_INDEX, 50000, "INR", 0.07, 0.25, 0.35, 2),
        (Segment.US_STOCK, 50000, "USD", 0.015, 0.03, 0.05, 6),
        (Segment.US_INDEX, 50000, "USD", 0.015, 0.03, 0.05, 6),
    ],
)
def test_matches_architecture_doc_table(
    segment, capital, currency, base_risk_pct, hard_ceiling_pct, max_premium_pct, max_concurrent
):
    config = get_segment_config(segment)
    assert config.capital == capital
    assert config.currency == currency
    assert config.base_risk_pct == base_risk_pct
    assert config.hard_ceiling_pct == hard_ceiling_pct
    assert config.max_premium_pct == max_premium_pct
    assert config.max_concurrent == max_concurrent


def test_new_gate_fields_present_and_positive():
    """These don't have doc-table values to check against — just confirm
    every segment actually has them and they're sane (positive, and loss
    limits/drawdown/exposure expressed as fractions <= 1)."""
    for segment in Segment:
        config = get_segment_config(segment)
        assert 0 < config.daily_loss_limit_pct <= 1
        assert 0 < config.weekly_loss_limit_pct <= 1
        assert config.weekly_loss_limit_pct >= config.daily_loss_limit_pct
        assert 0 < config.max_drawdown_pct <= 1
        assert 0 < config.exposure_cap_pct <= 1
        assert 0 <= config.min_liquidity_score <= 1
        assert config.reentry_cooldown_minutes > 0


def test_config_is_frozen():
    config = get_segment_config(Segment.NSE_STOCK)
    with pytest.raises(pydantic.ValidationError):
        config.capital = 100000  # type: ignore[misc]


def test_all_four_segments_load_without_error():
    for segment in Segment:
        get_segment_config(segment)
