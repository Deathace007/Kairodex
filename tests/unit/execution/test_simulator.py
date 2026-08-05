import datetime
from decimal import Decimal

import pytest

from kairodex.core.enums import Side
from kairodex.execution.costs import compute_nse_costs
from kairodex.execution.simulator import ShadowLogger, SimulatedBroker
from kairodex.execution.types import OrderRequest, QuoteSnapshot

_NOW = datetime.datetime(2026, 8, 5, 10, 0, tzinfo=datetime.UTC)


def _quote(**overrides: object) -> QuoteSnapshot:
    base = dict(
        bid=Decimal(98), ask=Decimal(102), bid_sz=1000, ask_sz=1000,
        quote_ts=_NOW, oi=10000, chain_complete=True,
    )
    base.update(overrides)
    return QuoteSnapshot(**base)  # type: ignore[arg-type]


def _order(**overrides: object) -> OrderRequest:
    base = dict(trade_id=1, instrument_id=1, side=Side.BUY, qty=100)
    base.update(overrides)
    return OrderRequest(**base)  # type: ignore[arg-type]


async def test_full_fill_no_cost_model():
    broker = SimulatedBroker()
    result = await broker.execute(_order(qty=100), _quote(), _NOW, attempt=1)
    assert not result.rejected
    assert result.filled_qty == 100
    assert result.remaining_qty == 0
    assert result.price == Decimal("101.2")  # same hand-computed fill as test_fills.py
    assert result.costs is None
    assert not result.expired
    # spread_bps=400, slippage_bps=120 — same hand-computed values as test_fills.py's
    # test_buy_fill_hand_computed; must survive the ExecutionResult wrapping.
    assert result.spread_bps == pytest.approx(400.0)
    assert result.slippage_bps == pytest.approx(120.0)


async def test_with_cost_model_computes_costs():
    broker = SimulatedBroker(cost_model=compute_nse_costs)
    result = await broker.execute(_order(qty=100), _quote(), _NOW, attempt=1)
    assert result.costs is not None
    # premium = 101.2 * 100 = 10120, BUY side
    expected = compute_nse_costs(Side.BUY, Decimal("101.2") * 100)
    assert result.costs.total == expected.total


async def test_partial_fill_not_expired_before_max_attempts():
    broker = SimulatedBroker(max_attempts=3)
    result = await broker.execute(_order(qty=500), _quote(ask_sz=1000), _NOW, attempt=1)
    assert result.filled_qty == 250  # alpha=0.25 * 1000
    assert result.remaining_qty == 250
    assert not result.expired  # attempt 1 of 3, remainder should re-attempt


async def test_partial_fill_expires_at_max_attempts():
    broker = SimulatedBroker(max_attempts=3)
    result = await broker.execute(_order(qty=500), _quote(ask_sz=1000), _NOW, attempt=3)
    assert result.remaining_qty == 250
    assert result.expired


async def test_rejected_fill_expires_at_max_attempts():
    broker = SimulatedBroker(max_attempts=3)
    stale = _quote(quote_ts=_NOW - datetime.timedelta(seconds=5))
    result = await broker.execute(_order(), stale, _NOW, attempt=3)
    assert result.rejected
    assert result.expired


async def test_rejected_fill_not_expired_before_max_attempts():
    broker = SimulatedBroker(max_attempts=3)
    stale = _quote(quote_ts=_NOW - datetime.timedelta(seconds=5))
    result = await broker.execute(_order(), stale, _NOW, attempt=1)
    assert result.rejected
    assert not result.expired


async def test_shadow_logger_computes_identical_fill_to_simulated_broker():
    """ARCHITECTURE.md §12, verbatim: "computes the identical fill.\""""
    broker = SimulatedBroker(cost_model=compute_nse_costs)
    shadow = ShadowLogger(broker)
    order, quote = _order(qty=100), _quote()

    direct = await broker.execute(order, quote, _NOW, attempt=1)
    via_shadow = await shadow.execute(order, quote, _NOW, attempt=1)

    assert direct.filled_qty == via_shadow.filled_qty
    assert direct.price == via_shadow.price
    assert direct.costs is not None and via_shadow.costs is not None
    assert direct.costs.total == via_shadow.costs.total
