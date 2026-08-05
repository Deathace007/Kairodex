"""American options (US equity/ETF options, incl. the SPY/QQQ/DIA/IWM
us_index proxies per ADR 0007) via a Cox-Ross-Rubinstein binomial tree.

ARCHITECTURE.md §8 names Bjerksund-Stensland (2002) as the target
closed-form model here. That formula's exercise-boundary calculation
needs a bivariate normal CDF (Genz/Drezner-Wesolowsky quadrature, ~15
magic constants) — reproducing it correctly from memory, on a money-path
formula, with nothing to check it against, is a real way to ship a
confidently-wrong price. ARCHITECTURE.md's own stated ceiling for BS2002
is "upgrade to CRR binomial if deep-ITM accuracy matters" — this ships
that upgrade path as the baseline instead: a few ms slower per quote,
but every line is standard backward induction, checkable by hand, and
validated against genuine no-arbitrage bounds in
tests/unit/test_bjerksund.py (not just "looks like a plausible number").
ponytail: swap in the true BS2002 closed form once there's a reference
implementation (e.g. QuantLib) on hand to validate against, if
per-quote pricing latency ever actually matters.
"""

from __future__ import annotations

import math

Flag = str  # "c" or "p"

_DEFAULT_STEPS = 200


def price(
    flag: Flag,
    s: float,
    k: float,
    t: float,
    r: float,
    b: float,
    sigma: float,
    *,
    steps: int = _DEFAULT_STEPS,
) -> float:
    """s=spot, k=strike, t=years to expiry, r=risk-free rate, b=cost of
    carry (b = r - dividend_yield for equities/ETFs; b = r for a
    non-dividend-paying underlying — see forward.implied_cost_of_carry to
    derive it from a forward estimate instead of a guessed yield),
    sigma=vol."""
    if flag not in ("c", "p"):
        raise ValueError(f"flag must be 'c' or 'p', got {flag!r}")
    if s <= 0 or k <= 0 or t <= 0 or sigma <= 0:
        raise ValueError(
            f"s, k, t, sigma must all be positive (got s={s}, k={k}, t={t}, sigma={sigma})"
        )
    if steps < 1:
        raise ValueError(f"steps must be >= 1, got {steps}")

    dt = t / steps
    up = math.exp(sigma * math.sqrt(dt))
    down = 1.0 / up
    growth = math.exp(b * dt)
    p_up = (growth - down) / (up - down)
    if not (0.0 <= p_up <= 1.0):
        # CRR's no-arbitrage condition (d < growth < u) needs sigma large
        # enough relative to sqrt(dt) — fails at unrealistically low vol
        # for the given step count. A bogus probability here doesn't stay
        # bogus: backward induction amplifies it exponentially over `steps`
        # (a real, caught-live example: sigma=1e-4 at 150 steps returned a
        # "price" of 3e+105). Fail closed rather than return that.
        raise ValueError(
            f"invalid CRR probability {p_up:.4g} for sigma={sigma}, steps={steps} — "
            "vol too low relative to the time step; increase steps or sigma"
        )
    discount = math.exp(-r * dt)

    def payoff(spot_at_node: float) -> float:
        return max(spot_at_node - k, 0.0) if flag == "c" else max(k - spot_at_node, 0.0)

    # Terminal payoffs, node i = i down-moves out of `steps` total moves.
    values = [payoff(s * up ** (steps - i) * down**i) for i in range(steps + 1)]

    for step in range(steps - 1, -1, -1):
        for i in range(step + 1):
            spot_at_node = s * up ** (step - i) * down**i
            continuation = discount * (p_up * values[i] + (1 - p_up) * values[i + 1])
            values[i] = max(continuation, payoff(spot_at_node))

    return values[0]
