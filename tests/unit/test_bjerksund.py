"""No py_vollib American reference to check against (see bjerksund.py's
module docstring for why this ships a CRR binomial tree instead of the
Bjerksund-Stensland closed form). Validated instead against genuine
no-arbitrage properties that any correct American pricer must satisfy —
these catch a wrong formula just as reliably as a reference value would,
without risking a *wrong* reference value from memory."""

import math

import pytest

from kairodex.pricing import bjerksund

_STEPS = 300  # enough to make the CRR/European agreement tight in tests below


def _european_price(
    flag: str, s: float, k: float, t: float, r: float, b: float, sigma: float
) -> float:
    """European price with cost-of-carry b == black76 priced off the
    matching forward S*exp(bT) — reuses the already-validated black76
    module as the reference, rather than a second hand-derived formula."""
    from kairodex.pricing import black76

    forward = s * math.exp(b * t)
    return black76.price(flag, forward, k, t, r, sigma)


@pytest.mark.parametrize("flag", ["c", "p"])
def test_american_at_least_intrinsic(flag):
    s, k, t, r, b, sigma = 100.0, 90.0, 1.0, 0.05, 0.0, 0.25
    intrinsic = max(s - k, 0.0) if flag == "c" else max(k - s, 0.0)
    val = bjerksund.price(flag, s, k, t, r, b, sigma, steps=_STEPS)
    assert val >= intrinsic - 1e-9


@pytest.mark.parametrize("flag", ["c", "p"])
def test_american_at_least_european(flag):
    """Early-exercise optionality can only add value, never subtract it."""
    s, k, t, r, b, sigma = 100.0, 100.0, 1.0, 0.06, 0.01, 0.3  # b < r: real dividend
    american = bjerksund.price(flag, s, k, t, r, b, sigma, steps=_STEPS)
    european = _european_price(flag, s, k, t, r, b, sigma)
    assert american >= european - 1e-6


def test_call_equals_european_when_no_dividend():
    """Classic no-arbitrage fact: an American call on a non-dividend-paying
    underlying (b >= r) is never optimal to exercise early, so it must
    equal the European price exactly."""
    s, k, t, r, b, sigma = 100.0, 90.0, 0.75, 0.05, 0.05, 0.2  # b == r
    american = bjerksund.price("c", s, k, t, r, b, sigma, steps=_STEPS)
    european = _european_price("c", s, k, t, r, b, sigma)
    # CRR converges at O(1/steps) — 300 steps gets within a few bp, not 1e-3.
    assert american == pytest.approx(european, abs=5e-3)


def test_put_early_exercise_premium_is_positive_when_deep_itm():
    """A deep ITM American put (no dividend) should be worth strictly more
    than its European counterpart — this is where early exercise is
    valuable even with b == r, unlike calls."""
    s, k, t, r, b, sigma = 40.0, 100.0, 1.0, 0.08, 0.08, 0.2
    american = bjerksund.price("p", s, k, t, r, b, sigma, steps=_STEPS)
    european = _european_price("p", s, k, t, r, b, sigma)
    assert american > european + 1e-3


@pytest.mark.parametrize("flag", ["c", "p"])
def test_monotonic_in_spot(flag):
    k, t, r, b, sigma = 100.0, 0.5, 0.05, 0.02, 0.25
    low = bjerksund.price(flag, 90.0, k, t, r, b, sigma, steps=_STEPS)
    mid = bjerksund.price(flag, 100.0, k, t, r, b, sigma, steps=_STEPS)
    high = bjerksund.price(flag, 110.0, k, t, r, b, sigma, steps=_STEPS)
    if flag == "c":
        assert low < mid < high
    else:
        assert low > mid > high


def test_converges_as_steps_increase():
    args = ("c", 100.0, 95.0, 1.0, 0.05, 0.0, 0.3)
    coarse = bjerksund.price(*args, steps=25)
    fine = bjerksund.price(*args, steps=800)
    assert coarse == pytest.approx(fine, abs=0.05)


def test_rejects_bad_flag():
    with pytest.raises(ValueError):
        bjerksund.price("x", 100, 100, 1, 0.05, 0.0, 0.2)


def test_rejects_nonpositive_inputs():
    with pytest.raises(ValueError):
        bjerksund.price("c", 100, 100, 0, 0.05, 0.0, 0.2)


def test_rejects_vol_too_low_for_crr_no_arbitrage_bound():
    """1bp vol at 150 steps breaches d < e^(b*dt) < u — a genuinely invalid
    tree parameterization, not just an imprecise one. Must fail closed
    (see the ValueError in bjerksund.py's docstring) rather than let the
    backward induction amplify it into a nonsense price."""
    with pytest.raises(ValueError):
        bjerksund.price("c", 100.0, 95.0, 0.5, 0.05, 0.02, 1e-4, steps=150)
