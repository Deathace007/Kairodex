import math

import pytest

from kairodex.pricing import black76, forward


def test_recovers_forward_from_black76_prices():
    """Generate call/put prices off a known forward, recover it via
    put-call parity — the actual production path (a real matched
    call/put quote pair -> forward.from_put_call_parity)."""
    true_forward, k, t, r, sigma = 23_500.0, 23_500.0, 0.05, 0.065, 0.15
    call = black76.price("c", true_forward, k, t, r, sigma)
    put = black76.price("p", true_forward, k, t, r, sigma)
    recovered = forward.from_put_call_parity(call, put, k, t, r)
    assert recovered == pytest.approx(true_forward, abs=1e-6)


def test_implied_cost_of_carry_round_trips():
    spot, b, t = 100.0, 0.02, 0.5
    fwd = spot * math.exp(b * t)
    assert forward.implied_cost_of_carry(spot, fwd, t) == pytest.approx(b, abs=1e-9)


def test_rejects_nonpositive_t():
    with pytest.raises(ValueError):
        forward.from_put_call_parity(5.0, 3.0, 100.0, 0.0, 0.05)
