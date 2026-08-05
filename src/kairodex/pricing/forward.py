"""Forward price derivation (ARCHITECTURE.md §8).

The whole point of pricing NSE options off a forward instead of the spot:
`from_put_call_parity` backs the forward out of two already-observed
option prices, which folds in the dividend yield and any repo/carry
effects automatically — nothing about the underlying's own dividend
schedule needs to be looked up or guessed.
"""

from __future__ import annotations

import math


def from_put_call_parity(
    call_price: float, put_price: float, strike: float, t: float, r: float
) -> float:
    """F = K + (C - P) * exp(rT), from Black-76 put-call parity
    C - P = exp(-rT) * (F - K). Use a liquid (ideally near-the-money)
    strike's quoted call/put mid prices — parity holds exactly only for
    European options with no early-exercise value, i.e. NSE, not the
    American US legs (see bjerksund.py)."""
    if t <= 0:
        raise ValueError(f"t must be positive, got {t}")
    return strike + (call_price - put_price) * math.exp(r * t)


def implied_cost_of_carry(spot: float, forward: float, t: float) -> float:
    """b such that F = S * exp(b*T) — the cost-of-carry `bjerksund.price`
    takes, backed out from a forward estimate instead of guessing a
    dividend yield directly. b = r for a non-dividend-paying/futures-style
    underlying; b < r when the market is pricing in dividends."""
    if spot <= 0 or t <= 0:
        raise ValueError(f"spot and t must be positive (got spot={spot}, t={t})")
    return math.log(forward / spot) / t
