import datetime
from decimal import Decimal

import pytest

from kairodex.core.enums import Segment
from kairodex.data.types import Bar, ChainSnapshot, Tick
from kairodex.features.compute.iv import iv_percentile, iv_rank, iv_skew, term_structure
from kairodex.features.types import FeatureContext

_T0 = datetime.datetime(2026, 8, 5, 9, 15, tzinfo=datetime.UTC)


def _leg(strike: float, option_type: str, iv: float, delta: float | None = None) -> Tick:
    return Tick(
        instrument_key=f"{strike}{option_type}",
        ts=_T0,
        strike=Decimal(str(strike)),
        option_type=option_type,
        iv=Decimal(str(iv)),
        delta=Decimal(str(delta)) if delta is not None else None,
    )


def _ctx_with_history(values: list[float]) -> FeatureContext:
    history = [(_T0 + datetime.timedelta(days=i), v) for i, v in enumerate(values)]
    return FeatureContext(as_of=_T0, segment=Segment.NSE_INDEX, iv_history=history)


def _snapshot(expiry: datetime.date, legs: list[Tick]) -> ChainSnapshot:
    return ChainSnapshot(underlying="TEST", expiry=expiry, ts=_T0, quotes=legs)


def _bar100() -> Bar:
    hundred = Decimal(100)
    return Bar(ts=_T0, open=hundred, high=hundred, low=hundred, close=hundred, volume=1)


def test_iv_rank_hand_computed():
    """values=[0.20,0.25,0.15,0.30,0.22], current=last=0.22. lo=0.15,
    hi=0.30. rank=(0.22-0.15)/(0.30-0.15)=0.07/0.15=0.466667."""
    result = iv_rank(_ctx_with_history([0.20, 0.25, 0.15, 0.30, 0.22]))
    assert result == pytest.approx(0.07 / 0.15, abs=1e-9)


def test_iv_rank_none_with_flat_history():
    assert iv_rank(_ctx_with_history([0.20, 0.20])) is None


def test_iv_percentile_hand_computed():
    """Same series, current=0.22, prior history=[0.20,0.25,0.15,0.30].
    Below 0.22: 0.20 (yes), 0.25 (no), 0.15 (yes), 0.30 (no) -> 2/4=0.5."""
    result = iv_percentile(_ctx_with_history([0.20, 0.25, 0.15, 0.30, 0.22]))
    assert result == pytest.approx(0.5, abs=1e-9)


def test_iv_skew_hand_computed():
    """Calls at delta 0.10/0.25/0.40 (iv 0.20/0.22/0.24), puts at delta
    -0.10/-0.25/-0.45 (iv 0.21/0.28/0.30). Nearest to target +-0.25 is the
    exact match in both cases -> call_iv=0.22, put_iv=0.28,
    skew=0.28-0.22=0.06."""
    legs = [
        _leg(110, "C", 0.20, delta=0.10),
        _leg(105, "C", 0.22, delta=0.25),
        _leg(100, "C", 0.24, delta=0.40),
        _leg(95, "P", 0.21, delta=-0.10),
        _leg(90, "P", 0.28, delta=-0.25),
        _leg(85, "P", 0.30, delta=-0.45),
    ]
    snapshot = _snapshot(datetime.date(2026, 9, 1), legs)
    ctx = FeatureContext(as_of=_T0, segment=Segment.NSE_INDEX, chain=[snapshot])
    result = iv_skew(ctx)
    assert result == pytest.approx(0.06, abs=1e-9)


def test_iv_skew_none_without_delta():
    legs = [_leg(100, "C", 0.22), _leg(100, "P", 0.24)]  # no delta populated
    snapshot = _snapshot(datetime.date(2026, 9, 1), legs)
    assert iv_skew(FeatureContext(as_of=_T0, segment=Segment.NSE_INDEX, chain=[snapshot])) is None


def test_term_structure_hand_computed():
    """spot=100. Front expiry: strike 95(C,iv=0.20)/95(P,iv=0.22),
    100(C,iv=0.25)/100(P,iv=0.27), 105(C,iv=0.30) -> nearest strike to 100
    is exactly 100 -> front_atm=avg(0.25,0.27)=0.26. Back expiry: same
    shape but strike-100 legs at iv 0.31/0.33 -> back_atm=0.32.
    term_structure = 0.32-0.26=0.06."""
    front_legs = [
        _leg(95, "C", 0.20),
        _leg(95, "P", 0.22),
        _leg(100, "C", 0.25),
        _leg(100, "P", 0.27),
        _leg(105, "C", 0.30),
    ]
    back_legs = [
        _leg(95, "C", 0.28),
        _leg(95, "P", 0.29),
        _leg(100, "C", 0.31),
        _leg(100, "P", 0.33),
        _leg(105, "C", 0.35),
    ]
    front = _snapshot(datetime.date(2026, 9, 1), front_legs)
    back = _snapshot(datetime.date(2026, 10, 1), back_legs)
    ctx = FeatureContext(
        as_of=_T0, segment=Segment.NSE_INDEX, chain=[front, back], underlying_bars=[_bar100()]
    )
    result = term_structure(ctx)
    assert result == pytest.approx(0.06, abs=1e-9)


def test_term_structure_none_with_single_expiry():
    legs = [_leg(100, "C", 0.25)]
    snapshot = _snapshot(datetime.date(2026, 9, 1), legs)
    ctx = FeatureContext(
        as_of=_T0, segment=Segment.NSE_INDEX, chain=[snapshot], underlying_bars=[_bar100()]
    )
    assert term_structure(ctx) is None
