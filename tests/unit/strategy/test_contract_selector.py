import datetime
from decimal import Decimal

from kairodex.core.enums import Side
from kairodex.strategy.contract_selector import ContractCandidate, select_contract

_AS_OF = datetime.date(2026, 8, 5)
_NEAR_EXPIRY = datetime.date(2026, 8, 20)  # 15 DTE
_FAR_EXPIRY = datetime.date(2027, 1, 1)  # way past max_dte


def _c(
    id_: int, strike: int, opt_type: str, expiry: datetime.date, bid: int, ask: int, delta: str
) -> ContractCandidate:
    return ContractCandidate(
        id_, Decimal(strike), opt_type, expiry, Decimal(bid), Decimal(ask), Decimal(delta)
    )


def _candidates() -> list[ContractCandidate]:
    return [
        _c(1, 100, "C", _NEAR_EXPIRY, 9, 11, "0.50"),
        _c(2, 110, "C", _NEAR_EXPIRY, 4, 6, "0.35"),
        _c(3, 120, "C", _NEAR_EXPIRY, 1, 2, "0.15"),
        _c(4, 100, "P", _NEAR_EXPIRY, 8, 10, "-0.45"),
        _c(5, 105, "C", _FAR_EXPIRY, 20, 22, "0.45"),
        _c(6, 95, "C", _NEAR_EXPIRY, 700, 720, "0.60"),
    ]


def _select(candidates, direction, **overrides):
    kwargs = dict(
        spot=Decimal(100), equity=Decimal(50000), max_premium_pct=0.35, lot_size=25, as_of=_AS_OF
    )
    kwargs.update(overrides)
    return select_contract(candidates, direction, **kwargs)


def test_picks_closest_delta_to_target_among_affordable_calls():
    """target_delta=0.40 (default). Affordable in-window calls: C1(delta
    0.50, dist 0.10), C2(delta 0.35, dist 0.05), C3(delta 0.15, dist
    0.25). C6 is unaffordable (mid=710*25=17750 > 17500), C5 is past
    max_dte. C2 has the smallest distance -> selected."""
    result = _select(_candidates(), Side.BUY)
    assert result.selected is not None
    assert result.selected.instrument_id == 2


def test_sell_direction_only_considers_puts():
    """Only one put candidate (id=4) exists, in-window and affordable
    (mid=9*25=225) -> selected regardless of delta targeting since it's
    the only candidate."""
    result = _select(_candidates(), Side.SELL)
    assert result.selected is not None
    assert result.selected.instrument_id == 4
    assert result.selected.option_type == "P"


def test_picks_closest_delta_to_target_among_affordable_puts():
    """target_delta=0.40 (default) -> signed target for puts is -0.40 (puts
    carry negative delta by this codebase's convention). Candidates: P1
    (delta -0.15, dist |-0.15 - -0.40| = 0.25), P2 (delta -0.40, dist 0.0),
    P3 (delta -0.60, dist 0.20). P2 has the smallest distance -> selected.
    Regression test: comparing signed put delta against the bare positive
    target_delta (the pre-fix bug) would instead pick P1 (dist
    |-0.15 - 0.40| = 0.55, smallest among 0.55/0.80/1.00) — the weakest
    put, exactly backwards."""
    puts = [
        _c(21, 100, "P", _NEAR_EXPIRY, 8, 10, "-0.15"),
        _c(22, 105, "P", _NEAR_EXPIRY, 8, 10, "-0.40"),
        _c(23, 110, "P", _NEAR_EXPIRY, 8, 10, "-0.60"),
    ]
    result = _select(puts, Side.SELL)
    assert result.selected is not None
    assert result.selected.instrument_id == 22


def test_no_candidates_in_expiry_window():
    only_far_calls = [c for c in _candidates() if c.option_type == "C" and c.expiry == _FAR_EXPIRY]
    result = _select(only_far_calls, Side.BUY)
    assert result.selected is None
    assert result.reason == "NO_CANDIDATES_IN_EXPIRY_WINDOW"


def test_no_affordable_contract():
    only_expensive = [c for c in _candidates() if c.instrument_id == 6]
    result = _select(only_expensive, Side.BUY)
    assert result.selected is None
    assert result.reason == "NO_AFFORDABLE_CONTRACT"


def test_affordability_boundary_exact_limit_passes():
    """max_premium = 0.35 * 50000 = 17500. A candidate priced at exactly
    that (mid=700, premium=700*25=17500) should be included, not excluded
    (<=, not <)."""
    candidate = ContractCandidate(9, Decimal(100), "C", _NEAR_EXPIRY, Decimal(699), Decimal(701))
    result = _select([candidate], Side.BUY)
    assert result.selected is not None


def test_falls_back_to_spot_distance_when_no_delta_available():
    """No delta on either candidate -> falls back to normalized distance
    from spot(100). strike=105 -> 0.05; strike=115 -> 0.15. Nearer one wins."""
    near = ContractCandidate(10, Decimal(105), "C", _NEAR_EXPIRY, Decimal(5), Decimal(6))
    far = ContractCandidate(11, Decimal(115), "C", _NEAR_EXPIRY, Decimal(2), Decimal(3))
    result = _select([far, near], Side.BUY)  # order shouldn't matter
    assert result.selected is not None
    assert result.selected.instrument_id == 10


def test_crossed_book_candidate_excluded():
    crossed = ContractCandidate(12, Decimal(100), "C", _NEAR_EXPIRY, Decimal(11), Decimal(9))
    result = _select([crossed], Side.BUY)
    assert result.selected is None
    assert result.reason == "NO_CANDIDATES_IN_EXPIRY_WINDOW"


def test_mid_price_property():
    result = _select(_candidates(), Side.BUY)
    assert result.selected is not None
    assert result.mid_price == (result.selected.bid + result.selected.ask) / 2


def test_mid_price_none_when_nothing_selected():
    result = _select([], Side.BUY)
    assert result.mid_price is None


def test_rejects_the_closest_available_when_still_too_far_from_target():
    """The gate found live 2026-08-06: on a thin US vendor, the only
    candidates with enough recorded volume to synthesize a fillable book
    were routinely far-OTM (delta ~0.005, a lottery ticket) — closest of a
    bad set is still a bad pick. Before this gate, `min(key=distance)`
    would have happily returned the 0.005-delta contract as 'selected'
    just because nothing better existed; now it's rejected outright."""
    lottery_ticket = _c(30, 400, "C", _NEAR_EXPIRY, 1, 2, "0.005")
    result = _select([lottery_ticket], Side.BUY)
    assert result.selected is None
    assert result.reason == "NO_CONTRACT_NEAR_TARGET_DELTA"


def test_deep_itm_is_also_rejected_not_just_deep_otm():
    """The same live finding cut both ways — a 0.95-delta contract (a
    leveraged stock substitute at a huge premium) is exactly as far from
    target_delta=0.40 as a lottery ticket, just on the other side."""
    near_stock = _c(31, 20, "C", _NEAR_EXPIRY, 80, 82, "0.95")
    result = _select([near_stock], Side.BUY)
    assert result.selected is None
    assert result.reason == "NO_CONTRACT_NEAR_TARGET_DELTA"


def test_exact_boundary_distance_still_selects():
    """0.25 is the default max_delta_distance; a candidate exactly at that
    distance (delta=0.15, dist=|0.15-0.40|=0.25) must still be accepted —
    the check is `>`, not `>=`, matching this codebase's own affordability
    boundary convention (test_affordability_boundary_exact_limit_passes)."""
    boundary = _c(32, 120, "C", _NEAR_EXPIRY, 1, 2, "0.15")
    result = _select([boundary], Side.BUY)
    assert result.selected is not None


def test_just_past_the_boundary_is_rejected():
    boundary_plus = _c(33, 121, "C", _NEAR_EXPIRY, 1, 2, "0.149")
    result = _select([boundary_plus], Side.BUY)
    assert result.selected is None
    assert result.reason == "NO_CONTRACT_NEAR_TARGET_DELTA"


def test_delta_cap_still_lets_a_reasonable_contract_through():
    """The gate must not become a blanket rejection — a genuinely
    near-target contract (the existing hand-picked fixture) still selects
    exactly as before this change."""
    result = _select(_candidates(), Side.BUY)
    assert result.selected is not None
    assert result.selected.instrument_id == 2  # unchanged from the original test above
