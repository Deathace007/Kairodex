import datetime
from decimal import Decimal

from kairodex.analytics import breakdowns
from kairodex.analytics.types import TradeRecord
from kairodex.core.enums import Segment


def _trade(
    *,
    opened_at: datetime.datetime,
    option_type: str = "C",
    strike: Decimal = Decimal(100),
    underlying_px: float = 100.0,
    vol_regime: float | None = 1.2,
    trend_state: float | None = 0.02,
    expiry: datetime.date | None = None,
    net_pnl: Decimal = Decimal(10),
    segment: Segment = Segment.NSE_STOCK,
) -> TradeRecord:
    return TradeRecord(
        trade_id=1,
        segment=segment,
        strategy_id=1,
        underlying_symbol="RELIANCE",
        instrument_symbol="RELIANCE26AUGCE",
        option_type=option_type,
        strike=strike,
        expiry=expiry,
        opened_at=opened_at,
        closed_at=opened_at + datetime.timedelta(hours=1),
        avg_entry=Decimal(10),
        avg_exit=Decimal(11),
        initial_stop_price=Decimal(9),
        gross_pnl=net_pnl,
        net_pnl=net_pnl,
        fees=Decimal(1),
        holding_secs=3600,
        exit_reason="TARGET",
        context_entry={
            "underlying_px": underlying_px,
            "vol_regime": vol_regime,
            "trend_state_strength": trend_state,
        },
        mfe=Decimal(15),
        mae=Decimal(-2),
    )


def test_weekday_bucket_groups_by_day_name():
    monday = datetime.datetime(2026, 8, 3, 10, 0, tzinfo=datetime.UTC)  # a real Monday
    tuesday = datetime.datetime(2026, 8, 4, 10, 0, tzinfo=datetime.UTC)
    result = breakdowns.breakdown([_trade(opened_at=monday), _trade(opened_at=tuesday)], "weekday")
    assert set(result) == {"Monday", "Tuesday"}
    assert result["Monday"].n_trades == 1


def test_session_bucket_open_mid_close():
    # NSE window 03:45-10:00 UTC — early/mid/late thirds
    early = datetime.datetime(2026, 8, 3, 4, 0, tzinfo=datetime.UTC)
    late = datetime.datetime(2026, 8, 3, 9, 45, tzinfo=datetime.UTC)
    result = breakdowns.breakdown([_trade(opened_at=early), _trade(opened_at=late)], "session")
    assert result["open"].n_trades == 1
    assert result["close"].n_trades == 1


def test_session_bucket_skips_trade_outside_window():
    outside = datetime.datetime(2026, 8, 3, 1, 0, tzinfo=datetime.UTC)  # before NSE open
    result = breakdowns.breakdown([_trade(opened_at=outside)], "session")
    assert result == {}


def test_us_session_bucket_uses_real_dst_offset_not_a_fixed_utc_window():
    # 2026-01-15 is EST (UTC-5): real session is 14:30-21:00 UTC (9:30-16:00
    # ET). A fixed 13:30-20:00 UTC window (EDT-only) would have put this
    # trade — 20:30 UTC = 15:30 ET, the real closing half-hour — *past* its
    # own close (frac > 1), silently dropping it from the breakdown
    # entirely instead of correctly bucketing it "close".
    winter_close = datetime.datetime(2026, 1, 15, 20, 30, tzinfo=datetime.UTC)
    result = breakdowns.breakdown(
        [_trade(opened_at=winter_close, segment=Segment.US_STOCK)], "session"
    )
    assert result["close"].n_trades == 1


def test_us_session_bucket_open_third_in_edt_summer():
    # 2026-08-03 is EDT (UTC-4): real session is 13:30-20:00 UTC.
    summer_open = datetime.datetime(2026, 8, 3, 13, 45, tzinfo=datetime.UTC)
    result = breakdowns.breakdown(
        [_trade(opened_at=summer_open, segment=Segment.US_STOCK)], "session"
    )
    assert result["open"].n_trades == 1


def test_expiry_bucket_days_to_expiry():
    opened = datetime.datetime(2026, 8, 3, 10, 0, tzinfo=datetime.UTC)
    near = _trade(opened_at=opened, expiry=datetime.date(2026, 8, 4))
    far = _trade(opened_at=opened, expiry=datetime.date(2026, 9, 15))
    result = breakdowns.breakdown([near, far], "expiry")
    assert set(result) == {"0-2d", "31d+"}


def test_moneyness_bucket_call_itm_vs_put_itm():
    opened = datetime.datetime(2026, 8, 3, 10, 0, tzinfo=datetime.UTC)
    # call, strike below spot -> ITM
    call_itm = _trade(opened_at=opened, option_type="C", strike=Decimal(90), underlying_px=100.0)
    # put, strike above spot -> ITM
    put_itm = _trade(opened_at=opened, option_type="P", strike=Decimal(110), underlying_px=100.0)
    result = breakdowns.breakdown([call_itm, put_itm], "moneyness")
    assert result["ITM"].n_trades == 2


def test_vol_regime_bucket_expanding_vs_contracting():
    opened = datetime.datetime(2026, 8, 3, 10, 0, tzinfo=datetime.UTC)
    expanding = _trade(opened_at=opened, vol_regime=1.5)
    contracting = _trade(opened_at=opened, vol_regime=0.7)
    result = breakdowns.breakdown([expanding, contracting], "vol_regime")
    assert result["expanding"].n_trades == 1
    assert result["contracting"].n_trades == 1


def test_regime_bucket_missing_context_is_skipped():
    opened = datetime.datetime(2026, 8, 3, 10, 0, tzinfo=datetime.UTC)
    t = _trade(opened_at=opened, trend_state=None)
    assert breakdowns.breakdown([t], "regime") == {}


def test_unknown_dimension_raises():
    import pytest

    with pytest.raises(ValueError, match="unknown breakdown dimension"):
        breakdowns.breakdown([], "not_a_real_dimension")
