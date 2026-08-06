import datetime
from decimal import Decimal

import pytest

from kairodex.core.enums import Segment
from kairodex.data.types import Bar, ChainSnapshot, Tick
from kairodex.features.compute.options_positioning import (
    liquidity_score,
    net_gamma_exposure,
    oi_change,
    oi_pcr,
)
from kairodex.features.types import FeatureContext

_T0 = datetime.datetime(2026, 8, 5, 9, 15, tzinfo=datetime.UTC)
_EXPIRY = datetime.date(2026, 9, 1)


def _leg(
    key: str, option_type: str, oi: int | None = None, gamma: float | None = None, **kw: object
) -> Tick:
    return Tick(
        instrument_key=key,
        ts=_T0,
        option_type=option_type,
        oi=oi,
        gamma=Decimal(str(gamma)) if gamma is not None else None,
        **kw,  # type: ignore[arg-type]
    )


def _snapshot(legs: list[Tick]) -> ChainSnapshot:
    return ChainSnapshot(underlying="TEST", expiry=_EXPIRY, ts=_T0, quotes=legs)


def _bar(close: float) -> Bar:
    c = Decimal(str(close))
    return Bar(ts=_T0, open=c, high=c, low=c, close=c, volume=1)


def test_oi_pcr_hand_computed():
    """put_oi=[100,200]=300, call_oi=[150,50]=200 -> pcr=300/200=1.5."""
    legs = [
        _leg("P1", "P", oi=100),
        _leg("P2", "P", oi=200),
        _leg("C1", "C", oi=150),
        _leg("C2", "C", oi=50),
    ]
    ctx = FeatureContext(as_of=_T0, segment=Segment.NSE_INDEX, chain=[_snapshot(legs)])
    assert oi_pcr(ctx) == pytest.approx(1.5, abs=1e-9)


def test_oi_pcr_none_when_no_call_oi():
    legs = [_leg("P1", "P", oi=100)]
    ctx = FeatureContext(as_of=_T0, segment=Segment.NSE_INDEX, chain=[_snapshot(legs)])
    assert oi_pcr(ctx) is None


def test_oi_change_hand_computed():
    """current={A:120,B:80,C:50}, prior={A:100,B:100} (C is new).
    changes: A:+20, B:-20, C:+50 -> total=50. total_prior=200.
    oi_change=50/200=0.25."""
    current = [_leg("A", "C", oi=120), _leg("B", "C", oi=80), _leg("C", "C", oi=50)]
    prior = [_leg("A", "C", oi=100), _leg("B", "C", oi=100)]
    ctx = FeatureContext(
        as_of=_T0,
        segment=Segment.NSE_INDEX,
        chain=[_snapshot(current)],
        prior_chain=[_snapshot(prior)],
    )
    assert oi_change(ctx) == pytest.approx(0.25, abs=1e-9)


def test_oi_change_none_without_prior():
    ctx = FeatureContext(
        as_of=_T0, segment=Segment.NSE_INDEX, chain=[_snapshot([_leg("A", "C", oi=100)])]
    )
    assert oi_change(ctx) is None


def test_net_gamma_exposure_hand_computed():
    """spot=100. call(gamma=0.05,oi=200), put(gamma=0.04,oi=300).
    total = 1*0.05*200 + (-1)*0.04*300 = 10 - 12 = -2.
    result = -2 * 100(multiplier) * 100^2(spot^2) * 0.01 = -2*100*10000*0.01 = -20000."""
    legs = [_leg("C1", "C", oi=200, gamma=0.05), _leg("P1", "P", oi=300, gamma=0.04)]
    ctx = FeatureContext(
        as_of=_T0, segment=Segment.NSE_INDEX, chain=[_snapshot(legs)], underlying_bars=[_bar(100)]
    )
    assert net_gamma_exposure(ctx) == pytest.approx(-20000.0, abs=1e-6)


def test_net_gamma_exposure_none_without_gamma():
    legs = [_leg("C1", "C", oi=200)]
    ctx = FeatureContext(
        as_of=_T0, segment=Segment.NSE_INDEX, chain=[_snapshot(legs)], underlying_bars=[_bar(100)]
    )
    assert net_gamma_exposure(ctx) is None


def test_liquidity_score_hand_computed():
    """ATM call: bid=9.5,ask=10.5 -> mid=10, spread_pct=1/10=0.1,
    spread_score=1/(1+0.1*10)=1/2=0.5. bid_sz=ask_sz=50 -> depth=100,
    depth_score=100/(100+100)=0.5. oi=1000 -> oi_score=1000/2000=0.5.
    volume=500 -> vol_score=500/1000=0.5. average=0.5."""
    leg = _leg(
        "C1",
        "C",
        oi=1000,
        strike=Decimal(100),
        bid=Decimal("9.5"),
        ask=Decimal("10.5"),
        bid_sz=50,
        ask_sz=50,
        volume=500,
    )
    ctx = FeatureContext(
        as_of=_T0, segment=Segment.NSE_INDEX, chain=[_snapshot([leg])], underlying_bars=[_bar(100)]
    )
    assert liquidity_score(ctx) == pytest.approx(0.5, abs=1e-9)


def test_liquidity_score_none_without_quotes():
    leg = _leg("C1", "C", oi=1000, strike=Decimal(100))  # no bid/ask
    ctx = FeatureContext(
        as_of=_T0, segment=Segment.NSE_INDEX, chain=[_snapshot([leg])], underlying_bars=[_bar(100)]
    )
    assert liquidity_score(ctx) is None


def test_liquidity_score_stays_none_on_nse_with_no_book():
    """The safety property that matters most: a missing NSE book must
    never fall back to a modelled one. NSE has a real book — a feed hiccup
    dropping bid/ask for one tick is a real "unknown," not a vendor
    limitation to paper over. Only Market.US gets the fallback."""
    leg = _leg("C1", "C", oi=1000, strike=Decimal(100), ltp=Decimal("10"), volume=5000)
    ctx = FeatureContext(
        as_of=_T0, segment=Segment.NSE_INDEX, chain=[_snapshot([leg])], underlying_bars=[_bar(100)]
    )
    assert liquidity_score(ctx) is None


def test_liquidity_score_falls_back_to_modelled_book_on_us():
    """The bug this regression-tests: contract selection could clear (once
    _candidates_from_chain synthesized a book) while this feature stayed
    permanently None for every US signal, because it read the raw,
    never-populated bid/ask instead of modelling one the same way. Live
    2026-08-06: 951 of ~1,100 recent us_stock signals died here."""
    leg = _leg("C1", "C", oi=1000, strike=Decimal(100), ltp=Decimal("10"), volume=5000)
    ctx = FeatureContext(
        as_of=_T0, segment=Segment.US_STOCK, chain=[_snapshot([leg])], underlying_bars=[_bar(100)]
    )
    assert liquidity_score(ctx) is not None
    assert 0.0 < liquidity_score(ctx) <= 1.0


def test_liquidity_score_searches_nearby_strikes_on_us_when_atm_is_too_thin():
    """The second bug behind 'US never trades': fixing the raw/synthesized
    mismatch alone still left this sampling one fixed strike, which real
    US chains usually have illiquid at the exact ATM even when a strike or
    two away is fine (measured live: well under 5% of legs on any US chain
    clear the fillable-volume bar). The literal ATM (strike 100) has real
    volume but too little to synthesize a fillable book; a nearby strike
    (105) has plenty. The window must find it rather than reporting
    LIQUIDITY_UNKNOWN with a tradeable leg one strike away."""
    thin_atm = _leg(
        "C1", "C", oi=10, strike=Decimal(100), ltp=Decimal("10"), volume=5
    )  # volume 5 -> synthesize_quote returns None (below _MIN_TOP_OF_BOOK)
    liquid_neighbor = _leg(
        "C2", "C", oi=1000, strike=Decimal(105), ltp=Decimal("8"), volume=5000
    )
    ctx = FeatureContext(
        as_of=_T0,
        segment=Segment.US_STOCK,
        chain=[_snapshot([thin_atm, liquid_neighbor])],
        underlying_bars=[_bar(100)],
    )
    score = liquidity_score(ctx)
    assert score is not None
    assert 0.0 < score <= 1.0


def test_liquidity_score_still_none_on_us_when_nothing_nearby_is_fillable():
    """The window is a search radius, not a lowered bar — when every
    nearby strike is genuinely too thin, the honest answer stays None,
    same as before this fix."""
    legs = [
        _leg("C1", "C", oi=10, strike=Decimal(100), ltp=Decimal("10"), volume=5),
        _leg("C2", "C", oi=10, strike=Decimal(101), ltp=Decimal("9"), volume=3),
    ]
    ctx = FeatureContext(
        as_of=_T0, segment=Segment.US_STOCK, chain=[_snapshot(legs)], underlying_bars=[_bar(100)]
    )
    assert liquidity_score(ctx) is None


def test_liquidity_score_none_on_us_with_no_last_price_either():
    """A contract that has never printed on this vendor either has no real
    book (the earlier case) or no evidence at all (no ltp) — both are
    genuinely unknown, not "probably fine," so both stay None."""
    leg = _leg("C1", "C", oi=1000, strike=Decimal(100))  # no bid/ask, no ltp
    ctx = FeatureContext(
        as_of=_T0, segment=Segment.US_STOCK, chain=[_snapshot([leg])], underlying_bars=[_bar(100)]
    )
    assert liquidity_score(ctx) is None
