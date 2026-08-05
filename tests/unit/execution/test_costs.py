"""Every expected value is worked by hand in each test's own docstring —
see kairodex/execution/costs.py's module docstring for the caveat these
rates carry (published values, not yet verified against a real contract
note)."""

from decimal import Decimal

import pytest

from kairodex.core.enums import Side
from kairodex.execution.costs import compute_nse_costs, compute_us_costs


def test_nse_sell_hand_computed():
    """premium=10000, SELL: brokerage=min(20, 0.0003*10000=3)=3.
    stt=0.001*10000=10 (sell only). exch=0.00035*10000=3.5.
    sebi=0.000001*10000=0.01. stamp=0 (sell, no stamp duty).
    regulatory_fees = 10+3.5+0.01+0 = 13.51.
    gst = 0.18*(3+3.5+0.01) = 0.18*6.51 = 1.1718.
    total = 3+13.51+1.1718 = 17.6818."""
    result = compute_nse_costs(Side.SELL, Decimal(10000))
    assert result.brokerage == pytest.approx(Decimal("3"))
    assert result.regulatory_fees == pytest.approx(Decimal("13.51"))
    assert result.taxes == pytest.approx(Decimal("1.1718"))
    assert result.total == pytest.approx(Decimal("17.6818"))


def test_nse_buy_hand_computed():
    """premium=10000, BUY: brokerage=3 (same). stt=0 (buy). exch=3.5.
    sebi=0.01. stamp=0.00003*10000=0.3 (buy only).
    regulatory_fees = 0+3.5+0.01+0.3 = 3.81.
    gst = 0.18*(3+3.5+0.01) = 1.1718 (unchanged — GST base excludes STT/stamp).
    total = 3+3.81+1.1718 = 7.9818."""
    result = compute_nse_costs(Side.BUY, Decimal(10000))
    assert result.brokerage == pytest.approx(Decimal("3"))
    assert result.regulatory_fees == pytest.approx(Decimal("3.81"))
    assert result.taxes == pytest.approx(Decimal("1.1718"))
    assert result.total == pytest.approx(Decimal("7.9818"))


def test_nse_brokerage_caps_at_flat_fee():
    """A large enough premium makes 0.03% exceed the Rs 20 flat cap —
    premium=1,000,000: 0.0003*1000000=300 > 20, so brokerage=20."""
    result = compute_nse_costs(Side.BUY, Decimal(1_000_000))
    assert result.brokerage == Decimal("20")


def test_nse_small_premium_uses_percentage_not_flat():
    """premium=1000: 0.0003*1000=0.3 < 20, so brokerage=0.3, not 20."""
    result = compute_nse_costs(Side.BUY, Decimal(1000))
    assert result.brokerage == pytest.approx(Decimal("0.3"))


def test_us_sell_hand_computed():
    """premium=500, qty=1, SELL: commission=0.65*1=0.65.
    occ=0.03*1=0.03. orf=0.02*1=0.02. sec_fee=0.000008*500=0.004 (sell).
    taf=0.00218*1=0.00218 (sell).
    regulatory_fees = 0.03+0.02+0.004+0.00218 = 0.05618.
    total = 0.65+0.05618+0 = 0.70618."""
    result = compute_us_costs(Side.SELL, Decimal(500), qty=1)
    assert result.brokerage == pytest.approx(Decimal("0.65"))
    assert result.regulatory_fees == pytest.approx(Decimal("0.05618"))
    assert result.taxes == Decimal(0)
    assert result.total == pytest.approx(Decimal("0.70618"))


def test_us_buy_hand_computed():
    """Same as above but BUY: sec_fee and taf don't apply.
    regulatory_fees = 0.03+0.02 = 0.05. total = 0.65+0.05 = 0.70."""
    result = compute_us_costs(Side.BUY, Decimal(500), qty=1)
    assert result.regulatory_fees == pytest.approx(Decimal("0.05"))
    assert result.total == pytest.approx(Decimal("0.70"))


def test_us_costs_scale_with_qty():
    """Per-contract fees scale linearly with qty; sec_fee (premium-based)
    does not scale with qty on its own — premium already reflects the
    full traded notional regardless of how many contracts it's split
    across."""
    one = compute_us_costs(Side.SELL, Decimal(500), qty=1)
    five = compute_us_costs(Side.SELL, Decimal(500), qty=5)
    assert five.brokerage == pytest.approx(one.brokerage * 5)


def test_costs_are_never_negative():
    for side in Side:
        assert compute_nse_costs(side, Decimal(10000)).total > 0
        assert compute_us_costs(side, Decimal(500), qty=1).total > 0


def test_both_cost_models_share_the_same_callable_interface():
    """SimulatedBroker's cost_model slot must accept either interchangeably
    — caught live once already (compute_nse_costs originally had no `qty`
    param while SimulatedBroker always calls cost_model(side, premium,
    qty) positionally)."""
    for cost_model in (compute_nse_costs, compute_us_costs):
        result = cost_model(Side.BUY, Decimal(1000), 1)
        assert result.total > 0
