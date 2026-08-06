"""The modelled book for US options (LSE publishes none). This prices real
fills, so the properties that keep it honest are pinned here — especially
the ones whose failure mode is silently flattering P&L rather than raising."""

from decimal import Decimal

from kairodex.execution.fills import _DEFAULT_MAX_SPREAD_BPS
from kairodex.execution.synthetic_quote import SPREAD_PCT, synthesize_quote


def test_quote_straddles_last_price():
    q = synthesize_quote(Decimal("10.00"), volume=1000)
    assert q is not None
    assert q.bid < Decimal("10.00") < q.ask
    # Symmetric: last price is the mid, so no directional bias is smuggled in.
    assert (q.bid + q.ask) / 2 == Decimal("10.00")


def test_spread_is_the_configured_fraction_of_premium():
    q = synthesize_quote(Decimal("10.00"), volume=1000)
    assert q is not None
    assert q.ask - q.bid == Decimal("10.00") * SPREAD_PCT


def test_spread_never_narrower_than_the_exchange_tick():
    """A modelled spread tighter than the tick the contract actually quotes
    in is not a spread — and being too tight is the direction that invents
    edge, so it gets a floor rather than a comment."""
    q = synthesize_quote(Decimal("0.05"), volume=1000)  # 4% of 0.05 = 0.002
    assert q is not None
    assert q.ask - q.bid >= Decimal("0.01")


def test_spread_stays_inside_the_fill_models_own_rejection_limit():
    """execution.fills rejects SPREAD_TOO_WIDE above 500bps. A synthetic
    quote that trips a policy meant for genuinely illiquid books would make
    US unable to trade for a second, unrelated reason."""
    for premium in ("0.50", "2.00", "10.00", "250.00"):
        q = synthesize_quote(Decimal(premium), volume=1000)
        assert q is not None
        mid = (q.bid + q.ask) / 2
        assert float((q.ask - q.bid) / mid) * 10000 < _DEFAULT_MAX_SPREAD_BPS


def test_no_last_price_means_no_quote():
    """A contract that has never printed has no evidence it can be bought at
    any price. Returning None drops it; returning a zero-width quote would
    have traded it."""
    assert synthesize_quote(None, volume=1000) is None
    assert synthesize_quote(Decimal(0), volume=1000) is None
    assert synthesize_quote(Decimal("-1"), volume=1000) is None


def test_zero_volume_yields_zero_size_so_the_fill_model_rejects_it():
    """The point of the volume proxy is this edge case: nobody traded this
    contract today, so there is no top of book to lift."""
    q = synthesize_quote(Decimal("10.00"), volume=0)
    assert q is not None
    assert q.bid_sz == 0 and q.ask_sz == 0

    q_none = synthesize_quote(Decimal("10.00"), volume=None)
    assert q_none is not None
    assert q_none.bid_sz == 0


def test_size_scales_with_volume():
    thin = synthesize_quote(Decimal("10.00"), volume=500)
    thick = synthesize_quote(Decimal("10.00"), volume=50_000)
    assert thin is not None and thick is not None
    assert thick.bid_sz > thin.bid_sz


def test_bid_never_goes_non_positive():
    """fills.compute_fill rejects INVALID_QUOTE on bid <= 0; a sub-tick
    premium must not produce one rather than being dropped here."""
    q = synthesize_quote(Decimal("0.005"), volume=1000)
    assert q is None or q.bid > 0
