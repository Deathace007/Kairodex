"""Central finite-difference Greeks (ARCHITECTURE.md §8) for pricers with
no closed-form derivative — currently just the American/bjerksund path;
Black-76's Greeks are analytic (see `black76.greeks`)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

_DS_REL = 1e-4  # relative spot bump
_D_SIGMA = 1e-4  # absolute vol bump
_D_T = 1.0 / 365.0  # one calendar day, for theta
_D_R = 1e-4  # absolute rate bump

PriceFn = Callable[[float, float, float, float], float]  # (s, t, r, sigma) -> price


@dataclass(frozen=True, slots=True)
class Greeks:
    delta: float
    gamma: float
    theta: float  # per calendar day
    vega: float  # per 1 vol point (1%)
    rho: float  # per 1 rate point (1%)


def central_diff_greeks(price_fn: PriceFn, s: float, t: float, r: float, sigma: float) -> Greeks:
    """price_fn(s, t, r, sigma) -> price, with strike/cost-of-carry/flag
    already closed over by the caller (see bjerksund.price's signature —
    typically `lambda s, t, r, sigma: bjerksund.price(flag, s, k, t, r, b, sigma)`).

    Note on rho: whether bumping `r` here also moves the dividend-derived
    cost of carry `b` is entirely up to how the caller's closure wires `b`
    to `r` — this function just bumps the `r` argument it's given.
    """
    h_s = s * _DS_REL
    p_center = price_fn(s, t, r, sigma)

    p_up_s, p_down_s = price_fn(s + h_s, t, r, sigma), price_fn(s - h_s, t, r, sigma)
    delta = (p_up_s - p_down_s) / (2 * h_s)
    gamma = (p_up_s - 2 * p_center + p_down_s) / (h_s * h_s)

    # Price decays as time passes, so bump time-to-expiry down, not up.
    t_bumped = max(t - _D_T, 1e-6)
    theta = price_fn(s, t_bumped, r, sigma) - p_center

    p_up_v, p_down_v = price_fn(s, t, r, sigma + _D_SIGMA), price_fn(s, t, r, sigma - _D_SIGMA)
    vega = (p_up_v - p_down_v) / (2 * _D_SIGMA) * 0.01

    p_up_r, p_down_r = price_fn(s, t, r + _D_R, sigma), price_fn(s, t, r - _D_R, sigma)
    rho = (p_up_r - p_down_r) / (2 * _D_R) * 0.01

    return Greeks(delta=delta, gamma=gamma, theta=theta, vega=vega, rho=rho)
