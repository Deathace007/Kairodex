"""Validated against py_vollib.black (ARCHITECTURE.md §8's explicit
correctness bar) rather than hand-checked textbook numbers — py_vollib's
Black-76 is battle-tested (Peter Jäckel's `lets_be_rational` under the
hood), so agreement with it is the real signal our from-scratch formulas
are right, not just plausible-looking."""

import math

import py_vollib.black as pv_black
import py_vollib.black.greeks.analytical as pv_greeks
import pytest

from kairodex.pricing import black76

# (F, K, T, r, sigma) — ITM/ATM/OTM, short/long-dated, calm/volatile
_CASES = [
    (49.0, 50.0, 0.3846, 0.05, 0.20),
    (100.0, 100.0, 0.5, 0.02, 0.20),
    (100.0, 80.0, 1.0, 0.06, 0.35),
    (100.0, 130.0, 0.05, 0.06, 0.15),
    (23500.0, 23500.0, 0.02, 0.065, 0.12),  # NIFTY-scale numbers
]


@pytest.mark.parametrize("f,k,t,r,sigma", _CASES)
@pytest.mark.parametrize("flag", ["c", "p"])
def test_price_matches_py_vollib(flag, f, k, t, r, sigma):
    ours = black76.price(flag, f, k, t, r, sigma)
    theirs = pv_black.black(flag, f, k, t, r, sigma)
    assert ours == pytest.approx(theirs, abs=1e-8, rel=1e-6)


@pytest.mark.parametrize("f,k,t,r,sigma", _CASES)
@pytest.mark.parametrize("flag", ["c", "p"])
def test_greeks_match_py_vollib(flag, f, k, t, r, sigma):
    ours = black76.greeks(flag, f, k, t, r, sigma)
    assert ours.delta == pytest.approx(pv_greeks.delta(flag, f, k, t, r, sigma), abs=1e-6)
    assert ours.gamma == pytest.approx(pv_greeks.gamma(flag, f, k, t, r, sigma), abs=1e-6)
    assert ours.vega == pytest.approx(pv_greeks.vega(flag, f, k, t, r, sigma), abs=1e-6)
    assert ours.theta == pytest.approx(pv_greeks.theta(flag, f, k, t, r, sigma), abs=1e-6)
    assert ours.rho == pytest.approx(pv_greeks.rho(flag, f, k, t, r, sigma), abs=1e-6)


def test_put_call_parity_holds():
    f, k, t, r, sigma = 100.0, 95.0, 0.5, 0.03, 0.25
    call = black76.price("c", f, k, t, r, sigma)
    put = black76.price("p", f, k, t, r, sigma)
    # C - P = exp(-rT) * (F - K)
    assert call - put == pytest.approx(math.exp(-r * t) * (f - k), abs=1e-9)


def test_rejects_bad_flag():
    with pytest.raises(ValueError):
        black76.price("x", 100, 100, 1, 0.05, 0.2)


def test_rejects_nonpositive_inputs():
    with pytest.raises(ValueError):
        black76.price("c", 100, 100, 0, 0.05, 0.2)
