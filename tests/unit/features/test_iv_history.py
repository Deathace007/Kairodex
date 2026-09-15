import datetime
from decimal import Decimal

from kairodex.core.enums import Segment
from kairodex.data.types import ChainSnapshot, Tick
from kairodex.features.compute.iv import iv_percentile, iv_rank
from kairodex.features.iv_history import MIN_PRIOR_SESSIONS, current_atm_iv, with_current
from kairodex.features.types import FeatureContext

_NOW = datetime.datetime(2026, 9, 15, 9, 0, tzinfo=datetime.UTC)


def _tick(delta: str, iv: str | None) -> Tick:
    return Tick(instrument_key="k", ts=_NOW, delta=Decimal(delta),
                vendor_iv=Decimal(iv) if iv is not None else None)


def test_current_atm_iv_skips_expiries_inside_min_dte_and_uses_near_the_money_legs():
    """On an expiry day the front contract's IV explodes (NIFTY 0.50 vs a
    0.13 history on 2026-09-15), so both the live reading and the stored
    history use the nearest expiry at least MIN_DTE days out."""
    today = datetime.date(2026, 9, 15)
    expiring = ChainSnapshot(underlying="NIFTY", expiry=today, ts=_NOW,
                             quotes=[_tick("0.50", "0.50")])
    next_week = ChainSnapshot(
        underlying="NIFTY", expiry=datetime.date(2026, 9, 22), ts=_NOW,
        quotes=[_tick("0.50", "0.20"), _tick("-0.45", "0.22"), _tick("0.10", "0.90"),
                _tick("0.55", None)],
    )
    later = ChainSnapshot(underlying="NIFTY", expiry=datetime.date(2026, 9, 29), ts=_NOW,
                          quotes=[_tick("0.50", "0.40")])
    value = current_atm_iv([later, expiring, next_week], today)
    assert value is not None and abs(value - 0.21) < 1e-12
    assert current_atm_iv([expiring], today) is None
    assert current_atm_iv([], today) is None


def test_with_current_needs_enough_history_and_a_current_reading():
    day = datetime.timedelta(days=1)
    prior = [(_NOW - day * i, 0.10 + i / 100) for i in range(MIN_PRIOR_SESSIONS, 0, -1)]
    assert with_current(prior[1:], 0.2, _NOW) == []
    assert with_current(prior, None, _NOW) == []
    history = with_current(prior, 0.30, _NOW)
    assert history[-1] == (_NOW, 0.30) and len(history) == MIN_PRIOR_SESSIONS + 1
    ctx = FeatureContext(as_of=_NOW, segment=Segment.NSE_INDEX, iv_history=history)
    assert iv_rank(ctx) == 1.0  # 0.30 is the highest reading in the window
    assert iv_percentile(ctx) == 1.0
