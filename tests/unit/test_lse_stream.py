from decimal import Decimal

from lse import OptionTick
from lse import Tick as LseTick

from kairodex.data.lse.client import _parse_stream_tick


def test_option_tick_maps_strike_and_right():
    lse_tick = OptionTick.from_symbol(
        symbol="AAPL250620C00200000",
        price=5.25,
        bid=5.1,
        ask=5.4,
        volume=120,
        timestamp="2026-06-04T16:32:00Z",
    )
    tick = _parse_stream_tick(lse_tick)
    assert tick is not None
    assert tick.instrument_key == "AAPL250620C00200000"
    assert tick.option_type == "C"
    assert tick.strike == Decimal("200")
    assert tick.ltp == Decimal("5.25")
    assert tick.bid == Decimal("5.1")
    assert tick.volume == 120


def test_put_contract_maps_to_p():
    lse_tick = OptionTick.from_symbol(symbol="TSLA250620P00150000", price=3.0)
    tick = _parse_stream_tick(lse_tick)
    assert tick is not None
    assert tick.option_type == "P"


def test_non_option_tick_has_no_strike_or_right():
    lse_tick = LseTick(symbol="BTC/USD", price=50000.0)
    tick = _parse_stream_tick(lse_tick)
    assert tick is not None
    assert tick.option_type is None
    assert tick.strike is None


def test_tick_with_no_price_is_dropped():
    lse_tick = LseTick(symbol="X", price=None)  # type: ignore[arg-type]
    assert _parse_stream_tick(lse_tick) is None
