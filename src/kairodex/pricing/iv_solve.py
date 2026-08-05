"""Implied volatility: Brent's method on price, bracketed
(ARCHITECTURE.md §8). Model-agnostic — works for both black76.price and
bjerksund.price via a `sigma -> price` closure, so this file has no
knowledge of which market it's solving for.

Fails closed: an unbracketed target price (outside what's achievable in
[lo, hi]) or a non-convergent search returns `None`, never a silently
wrong number — callers store `quality = NO_IV` on `None`, matching the
existing `quality` bitmask pattern in `kairodex.data.quality`.
"""

from __future__ import annotations

import math
from collections.abc import Callable

PriceFn = Callable[[float], float]  # sigma -> price

_DEFAULT_LO = 1e-4  # 1bp vol
_DEFAULT_HI = 5.0  # 500% vol — generous enough for any real quote
_DEFAULT_TOL = 1e-8
_DEFAULT_MAX_ITER = 100


def initial_guess(price: float, forward: float, t: float) -> float:
    """Brenner-Subrahmanyam: a simple closed-form ATM-ish starting point.
    ponytail: full Jäckel "Let's Be Rational" rational-approximation guess
    would converge in fewer iterations, but Brent doesn't need a good
    guess to be correct (unlike Newton) — it only needs a valid bracket,
    which `solve` establishes independently of this. Upgrade if iteration
    count ever actually matters (e.g. solving IV for every tick, not just
    on demand)."""
    if forward <= 0 or t <= 0:
        raise ValueError(f"forward and t must be positive (got forward={forward}, t={t})")
    return max((price / forward) * 2.506628274631 / math.sqrt(t), _DEFAULT_LO)


def solve(
    price_fn: PriceFn,
    target_price: float,
    *,
    lo: float = _DEFAULT_LO,
    hi: float = _DEFAULT_HI,
    tol: float = _DEFAULT_TOL,
    max_iter: int = _DEFAULT_MAX_ITER,
) -> float | None:
    """Solve price_fn(sigma) == target_price for sigma via Brent's method.
    Returns None if [lo, hi] doesn't bracket a root or convergence fails."""

    def f(sigma: float) -> float:
        return price_fn(sigma) - target_price

    def f_or_none(sigma: float) -> float | None:
        # A pricer can legitimately refuse to evaluate at an extreme input
        # (e.g. bjerksund.price rejects a numerically-invalid low-vol/coarse
        # -step combination rather than return a wrong number) — that's a
        # "no root here," not a crash.
        try:
            return f(sigma)
        except ValueError:
            return None

    a, b = lo, hi
    fa, fb = f_or_none(a), f_or_none(b)
    if fa is None or fb is None:
        return None
    if fa == 0:
        return a
    if fb == 0:
        return b
    if fa * fb > 0:
        return None  # target price not achievable anywhere in [lo, hi]

    if abs(fa) < abs(fb):
        a, b = b, a
        fa, fb = fb, fa

    c, fc = a, fa
    mflag = True
    d = a  # only ever read after mflag is False, when it holds a real previous point

    for _ in range(max_iter):
        if fb == 0 or abs(b - a) < tol:
            return b

        if fa != fc and fb != fc:
            # inverse quadratic interpolation
            s = (
                a * fb * fc / ((fa - fb) * (fa - fc))
                + b * fa * fc / ((fb - fa) * (fb - fc))
                + c * fa * fb / ((fc - fa) * (fc - fb))
            )
        else:
            # secant method
            s = b - fb * (b - a) / (fb - fa)

        lo_bound, hi_bound = (3 * a + b) / 4, b
        if lo_bound > hi_bound:
            lo_bound, hi_bound = hi_bound, lo_bound
        needs_bisect = (
            not (lo_bound < s < hi_bound)
            or (mflag and abs(s - b) >= abs(b - c) / 2)
            or (not mflag and abs(s - b) >= abs(c - d) / 2)
            or (mflag and abs(b - c) < tol)
            or (not mflag and abs(c - d) < tol)
        )
        if needs_bisect:
            s = (a + b) / 2
            mflag = True
        else:
            mflag = False

        fs = f(s)
        d = c
        c, fc = b, fb
        if fa * fs < 0:
            b, fb = s, fs
        else:
            a, fa = s, fs
        if abs(fa) < abs(fb):
            a, b = b, a
            fa, fb = fb, fa

    return b  # best estimate after max_iter — still close, just didn't hit tol
