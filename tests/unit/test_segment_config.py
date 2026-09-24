"""Every value asserted here is copy-checked against ARCHITECTURE.md
§11's own table, not just "does it load"."""

import pydantic
import pytest

from kairodex.config.segments import get_segment_config
from kairodex.core.enums import Segment


@pytest.mark.parametrize(
    "segment,capital,currency,base_risk_pct,hard_ceiling_pct,max_premium_pct,max_concurrent",
    [
        (Segment.NSE_STOCK, 50000, "INR", 0.08, 0.35, 0.35, 2),  # 5 -> 2, 2026-09-15
        (Segment.NSE_INDEX, 100000, "INR", 0.07, 0.25, 0.35, 1),  # capital 50k -> 100k, 2026-09-24
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


def test_every_segment_declares_a_confidence_floor_above_zero():
    """A missing or zero floor is the pre-2026-08-07 behaviour: slots go to
    whatever the watchlist iterates over first, and better setups later in
    the session are locked out on MAX_CONCURRENT. Per-segment because the
    observed score distributions differ by ~10x between them."""
    for segment in Segment:
        config = get_segment_config(segment)
        assert 0.0 < config.min_confidence < 1.0, segment


def test_2026_09_15_fix_plan_values():
    """Pinned so a later edit to either YAML fails loudly rather than
    silently changing what the replay measured.

    `scratch_exit_after_minutes` is back to the 09-15 value of 15 after a
    round trip: `backtest.exit_replay` re-measured it over full quote
    paths and picked 40 (§28), which then cost ~Rs 6,168 over six live
    sessions because that replay holds the trade population fixed and so
    cannot see slot occupancy (§29). Reverted 2026-09-24."""
    stock = get_segment_config(Segment.NSE_STOCK)
    assert (stock.entry_warmup_minutes, stock.scratch_exit_after_minutes) == (45, 15)
    assert stock.scratch_exit_min_mfe_pct == 0.03
    assert stock.max_confidence == 0.90
    assert (stock.breakeven_trigger_pct, stock.breakeven_floor_pct) == (0.10, 0.0)
    assert stock.min_dte == 0
    index = get_segment_config(Segment.NSE_INDEX)
    assert (index.min_confidence, index.min_dte, index.max_confidence) == (0.50, 7, None)
    us = get_segment_config(Segment.US_STOCK)
    assert (us.max_confidence, us.breakeven_trigger_pct, us.min_dte) == (None, None, 0)
