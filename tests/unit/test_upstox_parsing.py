"""Instrument classification is the one piece of P0 logic with real
consequences if it's wrong (a stock option misfiled as an index option
would corrupt segment isolation). Fixtures are trimmed real records pulled
from the live Upstox NSE instrument master on 2026-08-04."""

import datetime
from decimal import Decimal

from kairodex.core.enums import InstrumentKind, Segment
from kairodex.data.types import Timeframe
from kairodex.data.upstox.client import (
    UpstoxClient,
    _epoch_ms_to_ist_date,
    _parse_instrument,
)

_INDEX_OPTION = {
    "segment": "NSE_FO",
    "instrument_type": "PE",
    "underlying_symbol": "MIDCPNIFTY",
    "underlying_type": "INDEX",
    "instrument_key": "NSE_FO|50917",
    "trading_symbol": "MIDCPNIFTY 15225 PE 27 OCT 26",
    "strike_price": 15225.0,
    "expiry": 1793125799000,
    "lot_size": 120,
    "tick_size": 5.0,
}

_STOCK_OPTION = {
    "segment": "NSE_FO",
    "instrument_type": "CE",
    "underlying_symbol": "OFSS",
    "underlying_type": "EQUITY",
    "instrument_key": "NSE_FO|98917",
    "trading_symbol": "OFSS 11000 CE 27 OCT 26",
    "strike_price": 11000.0,
    "expiry": 1793125799000,
    "lot_size": 100,
    "tick_size": 5.0,
}

_STOCK_UNDERLYING = {
    "segment": "NSE_EQ",
    "instrument_type": "EQ",
    "instrument_key": "NSE_EQ|INE0LLY01014",
    "trading_symbol": "EIEL",
    "lot_size": 1,
    "tick_size": 1.0,
}

_INDEX_UNDERLYING = {
    "segment": "NSE_INDEX",
    "instrument_type": "INDEX",
    "instrument_key": "NSE_INDEX|Nifty 50",
    "trading_symbol": "NIFTY",
}

_IRRELEVANT = {
    "segment": "NCD_FO",  # currency derivatives — outside all four segments
    "instrument_type": "CE",
    "instrument_key": "NCD_FO|14294",
    "trading_symbol": "GBPINR 130.5 CE 14 AUG 26",
}


def test_index_option_gets_nse_index_segment_and_cash_settlement():
    rec = _parse_instrument(_INDEX_OPTION)
    assert rec is not None
    assert rec.kind is InstrumentKind.OPTION
    assert rec.segment is Segment.NSE_INDEX
    assert rec.settlement is not None and rec.settlement.value == "cash"
    assert rec.option_type == "P"
    assert rec.strike == Decimal("15225.0")
    assert rec.lot_size == 120


def test_stock_option_gets_nse_stock_segment_and_physical_settlement():
    rec = _parse_instrument(_STOCK_OPTION)
    assert rec is not None
    assert rec.kind is InstrumentKind.OPTION
    assert rec.segment is Segment.NSE_STOCK
    assert rec.settlement is not None and rec.settlement.value == "physical"
    assert rec.option_type == "C"


def test_underlyings_carry_no_segment():
    stock = _parse_instrument(_STOCK_UNDERLYING)
    index = _parse_instrument(_INDEX_UNDERLYING)
    assert stock is not None and stock.kind is InstrumentKind.UNDERLYING and stock.segment is None
    assert index is not None and index.kind is InstrumentKind.INDEX and index.segment is None


def test_irrelevant_segments_are_filtered_out():
    assert _parse_instrument(_IRRELEVANT) is None


def test_expiry_epoch_reads_in_ist_not_utc():
    # 1793125799000 ms = 2026-10-27T15:29:59+05:30 — reading the UTC instant's
    # own date would still land on 2026-10-27 for this particular timestamp,
    # so this also pins the value itself, not just the timezone plumbing.
    assert _epoch_ms_to_ist_date(1793125799000).isoformat() == "2026-10-27"


class _RecordingUpstox:
    """Only `bars` is exercised, and it only needs `_get` — building a real
    client would drag in auth and an HTTP pool for a URL-shape assertion."""

    def __init__(self) -> None:
        self.urls: list[str] = []

    async def _get(self, url: str, params: object = None) -> dict[str, object]:
        self.urls.append(url)
        return {"data": {"candles": []}}

    bars = UpstoxClient.bars


async def test_bars_also_fetches_intraday_when_the_window_includes_today():
    """v3's historical-candle endpoint serves nothing for the current
    trading day — verified live 2026-08-07 after the NSE close, where
    historical over 08-06..08-08 returned 375 bars all dated 08-06 while
    the intraday path returned that day's own 375. Without the second
    call, NSE bars could never advance past yesterday however often the
    refresh ran."""
    client = _RecordingUpstox()
    today = datetime.date.today()
    await client.bars(  # type: ignore[arg-type]
        "NSE_EQ|INE002A01018",
        Timeframe.ONE_MIN,
        today - datetime.timedelta(days=2),
        today + datetime.timedelta(days=1),
    )
    assert len(client.urls) == 2
    assert "/historical-candle/intraday/" in client.urls[1]


async def test_bars_skips_intraday_for_a_purely_historical_window():
    """A backtest pulling a closed date range must not pay a second call
    per underlying for a day it did not ask about."""
    client = _RecordingUpstox()
    await client.bars(  # type: ignore[arg-type]
        "NSE_EQ|INE002A01018",
        Timeframe.ONE_MIN,
        datetime.date(2026, 1, 5),
        datetime.date(2026, 1, 9),
    )
    assert len(client.urls) == 1
    assert "intraday" not in client.urls[0]
