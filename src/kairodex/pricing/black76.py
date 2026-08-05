"""Black-76: European options priced off a forward rather than the spot —
the model for NSE options (ARCHITECTURE.md §8). Deriving the forward `F`
via put-call parity (`forward.py`) folds the dividend yield and any
repo/carry effects in automatically, so the only external input left is
`r` — a small, published number (e.g. the RBI repo/T-bill rate), not
something to guess per-instrument the way a dividend yield would be.

Formulas and conventions (theta per calendar day, vega/rho per 1-point
move) match `py_vollib.black` exactly — cross-checked in
tests/unit/test_black76.py, which is the real correctness check for the
hand-derived formulas below, not the docstrings.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

Flag = str  # "c" or "p"


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _check_flag(flag: Flag) -> None:
    if flag not in ("c", "p"):
        raise ValueError(f"flag must be 'c' or 'p', got {flag!r}")


def _d1_d2(f: float, k: float, t: float, sigma: float) -> tuple[float, float]:
    if f <= 0 or k <= 0 or t <= 0 or sigma <= 0:
        raise ValueError(
            f"f, k, t, sigma must all be positive (got f={f}, k={k}, t={t}, sigma={sigma})"
        )
    d1 = (math.log(f / k) + 0.5 * sigma * sigma * t) / (sigma * math.sqrt(t))
    d2 = d1 - sigma * math.sqrt(t)
    return d1, d2


def price(flag: Flag, f: float, k: float, t: float, r: float, sigma: float) -> float:
    """f=forward, k=strike, t=years to expiry, r=risk-free rate, sigma=vol."""
    _check_flag(flag)
    d1, d2 = _d1_d2(f, k, t, sigma)
    discount = math.exp(-r * t)
    if flag == "c":
        return discount * (f * _norm_cdf(d1) - k * _norm_cdf(d2))
    return discount * (k * _norm_cdf(-d2) - f * _norm_cdf(-d1))


@dataclass(frozen=True, slots=True)
class Greeks:
    delta: float
    gamma: float
    theta: float  # per calendar day
    vega: float  # per 1 vol point (1%)
    rho: float  # per 1 rate point (1%)


def greeks(flag: Flag, f: float, k: float, t: float, r: float, sigma: float) -> Greeks:
    _check_flag(flag)
    d1, d2 = _d1_d2(f, k, t, sigma)
    discount = math.exp(-r * t)
    pdf_d1 = _norm_pdf(d1)
    sqrt_t = math.sqrt(t)

    delta = discount * _norm_cdf(d1) if flag == "c" else -discount * _norm_cdf(-d1)

    gamma = discount * pdf_d1 / (f * sigma * sqrt_t)
    vega = f * discount * pdf_d1 * sqrt_t * 0.01

    first_term = f * discount * pdf_d1 * sigma / (2 * sqrt_t)
    if flag == "c":
        rate_term = r * f * discount * _norm_cdf(d1) - r * k * discount * _norm_cdf(d2)
    else:
        rate_term = -r * f * discount * _norm_cdf(-d1) + r * k * discount * _norm_cdf(-d2)
    theta = (-first_term + rate_term) / 365.0

    rho = -t * price(flag, f, k, t, r, sigma) * 0.01

    return Greeks(delta=delta, gamma=gamma, theta=theta, vega=vega, rho=rho)
