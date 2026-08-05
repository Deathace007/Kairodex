"""Validated against black76's analytic Greeks: feed black76.price through
the finite-difference machinery and confirm it recovers what the
closed-form formulas already give exactly. That's a stronger check than
testing the bjerksund path alone, where there's no independent ground
truth to compare against."""

import pytest

from kairodex.pricing import black76, greeks


@pytest.mark.parametrize("flag", ["c", "p"])
def test_matches_black76_analytic_greeks(flag):
    f, k, t, r, sigma = 100.0, 95.0, 0.5, 0.04, 0.25

    def price_fn(s: float, t: float, r: float, sigma: float) -> float:
        # black76 takes a forward, not a spot — with b=r this file's `s`
        # argument name is a slight abuse, standing in for "the thing being
        # bumped," matching how bjerksund.price actually gets closed over
        # in production (s is genuinely the spot there).
        return black76.price(flag, s, k, t, r, sigma)

    fd = greeks.central_diff_greeks(price_fn, f, t, r, sigma)
    analytic = black76.greeks(flag, f, k, t, r, sigma)

    assert fd.delta == pytest.approx(analytic.delta, abs=1e-4)
    assert fd.gamma == pytest.approx(analytic.gamma, abs=1e-4)
    assert fd.vega == pytest.approx(analytic.vega, abs=1e-4)
    assert fd.theta == pytest.approx(analytic.theta, abs=1e-4)
    assert fd.rho == pytest.approx(analytic.rho, abs=1e-4)
