"""Round-trip tests: generate a price at a known sigma, solve back for
sigma, confirm we recover it. Model-agnostic by design, so both black76
and bjerksund exercise the same solver — no separate American IV test
file needed."""

import pytest

from kairodex.pricing import bjerksund, black76, iv_solve


@pytest.mark.parametrize("flag", ["c", "p"])
@pytest.mark.parametrize("true_sigma", [0.05, 0.15, 0.30, 0.75, 2.0])
def test_recovers_known_sigma_black76(flag, true_sigma):
    f, k, t, r = 100.0, 95.0, 0.5, 0.04
    target = black76.price(flag, f, k, t, r, true_sigma)
    solved = iv_solve.solve(lambda sigma: black76.price(flag, f, k, t, r, sigma), target)
    assert solved is not None
    assert solved == pytest.approx(true_sigma, abs=1e-6)


@pytest.mark.parametrize("flag", ["c", "p"])
def test_recovers_known_sigma_bjerksund(flag):
    s, k, t, r, b, true_sigma = 100.0, 95.0, 0.5, 0.05, 0.02, 0.25
    target = bjerksund.price(flag, s, k, t, r, b, true_sigma, steps=150)

    def price_fn(sigma: float) -> float:
        return bjerksund.price(flag, s, k, t, r, b, sigma, steps=150)

    # lo=0.01 not the module default (1e-4): at 150 steps, sub-1%-vol
    # breaches CRR's no-arbitrage bound (see test below) — a real caller
    # solving American IV picks a bracket the tree can actually evaluate.
    solved = iv_solve.solve(price_fn, target, lo=0.01, tol=1e-6, max_iter=60)
    assert solved is not None
    # Binomial tree isn't perfectly smooth in sigma at low step counts —
    # looser tolerance than the closed-form black76 round-trip above.
    assert solved == pytest.approx(true_sigma, abs=5e-3)


def test_unevaluable_bracket_endpoint_returns_none_not_crash():
    """Caught live: bjerksund's default lo=1e-4 bracket endpoint breaches
    the binomial tree's no-arbitrage probability bound at coarse step
    counts (an invalid, not just imprecise, CRR parameterization) —
    solve() must fail closed, not propagate the pricer's ValueError."""
    s, k, t, r, b, sigma = 100.0, 95.0, 0.5, 0.05, 0.02, 0.25
    target = bjerksund.price("c", s, k, t, r, b, sigma, steps=150)
    solved = iv_solve.solve(
        lambda sig: bjerksund.price("c", s, k, t, r, b, sig, steps=150), target
    )  # default lo=1e-4 — the pathological endpoint
    assert solved is None


def test_unreachable_price_returns_none():
    f, k, t, r = 100.0, 95.0, 0.5, 0.04
    # A price far above what any vol in [lo, hi] can produce.
    absurd_price = 10_000.0
    solved = iv_solve.solve(lambda sigma: black76.price("c", f, k, t, r, sigma), absurd_price)
    assert solved is None


def test_initial_guess_is_positive_and_reasonable():
    guess = iv_solve.initial_guess(price=5.0, forward=100.0, t=0.5)
    assert 0 < guess < 5.0
