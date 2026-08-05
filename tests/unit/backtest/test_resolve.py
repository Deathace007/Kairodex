import datetime
from decimal import Decimal

from kairodex.backtest.resolve import resolve_forward_outcome
from kairodex.core.enums import Side
from kairodex.data.types import Bar


def _bar(ts_day: int, o: float, h: float, low: float, c: float) -> Bar:
    return Bar(
        ts=datetime.datetime(2026, 1, ts_day, tzinfo=datetime.UTC),
        open=Decimal(str(o)),
        high=Decimal(str(h)),
        low=Decimal(str(low)),
        close=Decimal(str(c)),
        volume=1000,
    )


def test_buy_hits_target_on_second_bar():
    """entry=100, atr=2 -> stop=98, target=104. Bar 1 stays inside both
    (favorable so far = 101-100=1, adverse so far = 100-99.5=0.5). Bar 2's
    high=104.5 touches target=104 first (checked before mae/mfe would
    matter) -> TARGET at 104, after 2 bars. mfe = max(1, 104.5-100=4.5) =
    4.5 -> mfe_atr=2.25. mae stays 0.5 -> mae_atr=0.25.
    return_atr = (104-100)/2 = 2.0."""
    bars = [_bar(1, 100, 101, 99.5, 100.5), _bar(2, 100.5, 104.5, 100, 104)]
    out = resolve_forward_outcome(Side.BUY, Decimal(100), Decimal(2), bars)
    assert out is not None
    assert out.exit_reason == "TARGET"
    assert out.exit_price == Decimal(104)
    assert out.bars_held == 2
    assert out.mfe == Decimal("4.5")
    assert out.mfe_atr == 2.25
    assert out.mae == Decimal("0.5")
    assert out.mae_atr == 0.25
    assert out.return_atr == 2.0


def test_sell_hits_stop_on_first_bar():
    """SELL entry=100, atr=2 -> stop=102, target=96. Bar high=102.5 >=
    stop(102) -> STOP at 102, after 1 bar."""
    bars = [_bar(1, 100, 102.5, 99, 99.5)]
    out = resolve_forward_outcome(Side.SELL, Decimal(100), Decimal(2), bars)
    assert out is not None
    assert out.exit_reason == "STOP"
    assert out.exit_price == Decimal(102)
    assert out.bars_held == 1
    # return_atr = -1 * (102-100)/2 = -1.0 (a loss, sign-adjusted for SELL)
    assert out.return_atr == -1.0


def test_times_out_within_max_holding_bars():
    """Neither stop(98) nor target(104) touched within 1 bar -> TIME,
    exit at that bar's close."""
    bars = [_bar(1, 100, 100.5, 99.8, 100.2)]
    out = resolve_forward_outcome(Side.BUY, Decimal(100), Decimal(2), bars, max_holding_bars=1)
    assert out is not None
    assert out.exit_reason == "TIME"
    assert out.exit_price == Decimal("100.2")
    assert out.bars_held == 1


def test_max_holding_bars_truncates_lookahead():
    """A target-hitting bar beyond max_holding_bars must never be seen —
    only bar 1 (inside both stop/target) is considered when
    max_holding_bars=1, so this times out instead of hitting the target
    bar 2 would have produced."""
    bars = [_bar(1, 100, 101, 99.8, 100.5), _bar(2, 100.5, 200, 100, 150)]
    out = resolve_forward_outcome(Side.BUY, Decimal(100), Decimal(2), bars, max_holding_bars=1)
    assert out is not None
    assert out.exit_reason == "TIME"
    assert out.bars_held == 1


def test_ambiguous_same_bar_resolves_to_stop():
    """A single bar whose range spans both stop(98) and target(104) can't
    tell us which happened first intrabar -> conservative: stop wins."""
    bars = [_bar(1, 100, 105, 97, 101)]
    out = resolve_forward_outcome(Side.BUY, Decimal(100), Decimal(2), bars)
    assert out is not None
    assert out.exit_reason == "STOP"


def test_no_forward_bars_returns_none():
    assert resolve_forward_outcome(Side.BUY, Decimal(100), Decimal(2), []) is None


def test_zero_atr_returns_none():
    bars = [_bar(1, 100, 101, 99, 100.5)]
    assert resolve_forward_outcome(Side.BUY, Decimal(100), Decimal(0), bars) is None


def test_negative_atr_returns_none():
    bars = [_bar(1, 100, 101, 99, 100.5)]
    assert resolve_forward_outcome(Side.BUY, Decimal(100), Decimal(-1), bars) is None
