"""`_DEFAULT_TARGET_DELTA` is a cost decision, not a style preference.

It sits inside the trade's own breakeven equation —

    required underlying move = (spread + theta_over_hold) / (delta * spot)

— where both the numerator and `delta` move with strike, so the function
has an interior minimum rather than being monotone in "how OTM do we
want to be". It was 0.40 on convention from P3 until 2026-08-14, carrying
a note to recalibrate "once backtesting can measure what delta actually
performs".

Measured over 778k liquid nse_stock chain quotes (OI > 500k) across five
sessions, in ATR of the underlying so it is directly comparable with
`signals.forward_outcome`, the total hurdle by delta band was:

    0.15-0.25  1.843 ATR      0.45-0.55  1.125 ATR   <- here
    0.25-0.35  1.486          0.55-0.65  1.068       <- aggregate min
    0.35-0.45  1.276  <- old  0.65-0.75  1.095
                              0.75-1.00  1.947

0.45-0.55 beat 0.35-0.45 in ALL FIVE sessions individually, so the move
is not one day's artefact. It stops short of the 0.55-0.65 aggregate
minimum on purpose: that basin is flat (0.06 ATR) and deeper ITM spends
gamma — the convexity PROGRESS.md §19d's ladder argument rests on — plus
premium per lot, which binds against `max_premium_pct`.

This file exists because of how the trailing stop drifted: §19d moved
`_DEFAULT_STOP_LOSS_PCT` 0.30 -> 0.20 and correctly moved the R-rungs
with it, but `evaluate_exits`' own `trail_pct` stayed at 0.30 in another
module and nothing failed. A constant with evidence behind it should
break loudly when it moves, so the evidence gets revisited with it.
"""

from decimal import Decimal

from kairodex.strategy.contract_selector import (
    _DEFAULT_MAX_DELTA_DISTANCE,
    _DEFAULT_TARGET_DELTA,
)


def test_target_delta_is_the_measured_value():
    """Fails loudly if the constant moves without this file's evidence
    block being updated to say why."""
    assert _DEFAULT_TARGET_DELTA == 0.50


def test_admissible_band_excludes_the_worst_measured_bucket():
    """The band is `target +- max_delta_distance`. At 0.50/0.25 that is
    0.25-0.75, which drops 0.15-0.25 (1.843 ATR, the worst admissible
    band under the old 0.40 target) and admits 0.65-0.75 (1.095 ATR,
    better than the old target's own 1.276)."""
    lo = _DEFAULT_TARGET_DELTA - _DEFAULT_MAX_DELTA_DISTANCE
    hi = _DEFAULT_TARGET_DELTA + _DEFAULT_MAX_DELTA_DISTANCE
    assert lo == 0.25
    assert hi == 0.75


def test_band_stays_inside_the_measured_basin():
    """Every admissible delta must sit in a band that was actually
    measured, not extrapolated. The measurement covered 0.01-1.00 and the
    basin under 1.5 ATR runs 0.25-0.75 — the band must not exceed it."""
    assert _DEFAULT_TARGET_DELTA - _DEFAULT_MAX_DELTA_DISTANCE >= 0.25
    assert _DEFAULT_TARGET_DELTA + _DEFAULT_MAX_DELTA_DISTANCE <= 0.75


def test_atm_is_preferred_over_the_old_target_by_distance():
    """Behavioural consequence, not just the number: given an ATM leg and
    the old 0.40-delta leg side by side, the selector now prefers ATM."""
    from datetime import date

    from kairodex.core.enums import Side
    from kairodex.strategy.contract_selector import ContractCandidate, select_contract

    expiry = date(2026, 8, 25)
    atm = ContractCandidate(1, Decimal(100), "C", expiry, Decimal(9), Decimal(11), Decimal("0.50"))
    otm = ContractCandidate(2, Decimal(110), "C", expiry, Decimal(4), Decimal(6), Decimal("0.40"))
    result = select_contract(
        [atm, otm],
        Side.BUY,
        spot=Decimal(100),
        equity=Decimal(50000),
        max_premium_pct=0.35,
        lot_size=25,
        as_of=date(2026, 8, 14),
    )
    assert result.selected is not None
    assert result.selected.instrument_id == 1
