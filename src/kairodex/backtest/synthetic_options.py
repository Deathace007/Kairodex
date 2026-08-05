"""Synthetic-option overlay (ARCHITECTURE.md §13, second pass): prices a
Black-76 option along the SAME backtested underlying path Track A
already resolved, at a flat IV assumption — answers "is this move big
enough and fast enough to beat theta and IV crush?" Labelled `ESTIMATE`
everywhere and **never counted toward a promotion gate** — `promotion.py`
never reads this module's output; only `metrics.DirectionalMetrics` (real
Track A gates) and Track B's live shadow numbers (real Track B gates) do.

Two deliberate simplifications, both because a Track A backtest has no
option chain to be more precise with — real strike/IV-path risk is
exactly what Track B's live shadow mode measures instead:

- **ATM at entry** (`strike = entry spot`), not delta-targeted like the
  real `contract_selector` — the simplest defensible proxy for "is the
  move worth the premium," not a precision claim.
- **A single flat IV held constant across the whole path** — no
  term-structure, skew, or vol-of-vol.
"""

from __future__ import annotations

from dataclasses import dataclass

from kairodex.backtest.types import BacktestSignal
from kairodex.core.enums import Side
from kairodex.pricing import black76

_DEFAULT_ASSUMED_DTE_DAYS = 30.0  # ponytail: one representative near-monthly
# expiry, inside contract_selector's own [1, 45] DTE window — first-pass,
# not derived from anything; recalibrate once real chain history exists.
_MIN_T_YEARS = 1.0 / 365.25  # floor so Black-76's t>0 requirement never trips near "expiry"
_DAYS_PER_YEAR = 365.25


@dataclass(frozen=True, slots=True)
class SyntheticOptionOutcome:
    entry_price: float  # ESTIMATE — synthetic option premium at signal
    exit_price: float  # ESTIMATE — synthetic option premium at resolved exit
    pnl_pct: float  # (exit - entry) / entry — ESTIMATE, never a promotion gate input


def price_synthetic_overlay(
    signal: BacktestSignal,
    *,
    iv: float,
    bar_days: float = 1.0,
    assumed_dte_days: float = _DEFAULT_ASSUMED_DTE_DAYS,
    r: float = 0.0,
) -> SyntheticOptionOutcome | None:
    """`iv` is the caller's to resolve ("an IV assumption drawn from the
    current surface," ARCHITECTURE.md §13) — this stays a pure function of
    (signal, iv), not a DB read, matching every other pure pricing/fill
    function in this codebase. `None` when there's nothing to price
    (unresolved signal, or a non-positive IV)."""
    if signal.outcome is None or iv <= 0:
        return None
    flag = "c" if signal.direction is Side.BUY else "p"
    strike = float(signal.entry_price)  # ATM at entry — see module docstring
    f_entry = float(signal.entry_price)
    f_exit = float(signal.outcome.exit_price)
    t_entry = assumed_dte_days / _DAYS_PER_YEAR
    days_elapsed = signal.outcome.bars_held * bar_days
    t_exit = max(_MIN_T_YEARS, (assumed_dte_days - days_elapsed) / _DAYS_PER_YEAR)

    entry_price = black76.price(flag, f_entry, strike, t_entry, r, iv)
    exit_price = black76.price(flag, f_exit, strike, t_exit, r, iv)
    if entry_price <= 0:
        return None
    return SyntheticOptionOutcome(
        entry_price=entry_price,
        exit_price=exit_price,
        pnl_pct=(exit_price - entry_price) / entry_price,
    )
