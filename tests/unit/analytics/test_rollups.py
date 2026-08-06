import datetime
from decimal import Decimal

from kairodex.analytics import rollups
from kairodex.analytics.types import EquityPoint

_TS = datetime.datetime(2026, 8, 3, 4, 0, tzinfo=datetime.UTC)


def _point(days: int, hours: int, equity: Decimal, drawdown: Decimal = Decimal(0)) -> EquityPoint:
    return EquityPoint(
        ts=_TS + datetime.timedelta(days=days, hours=hours),
        equity=equity,
        high_water_mark=equity,
        drawdown=drawdown,
        exposure=Decimal(0),
    )


def test_daily_rollup_open_close_high_low():
    points = [
        _point(0, 0, Decimal(50000)),
        _point(0, 3, Decimal(51000)),
        _point(0, 6, Decimal(49500), drawdown=Decimal("0.03")),
        _point(1, 0, Decimal(52000)),
    ]
    result = rollups.rollup(points, "daily")
    assert len(result) == 2
    day0 = result[0]
    assert day0.open_equity == Decimal(50000)
    assert day0.close_equity == Decimal(49500)
    assert day0.high_equity == Decimal(51000)
    assert day0.low_equity == Decimal(49500)
    assert day0.max_drawdown_pct == Decimal("0.03")


def test_weekly_rollup_period_key_format():
    result = rollups.rollup([_point(0, 0, Decimal(50000))], "weekly")
    assert len(result) == 1
    assert "-W" in result[0].period


def test_monthly_rollup_period_key_format():
    result = rollups.rollup([_point(0, 0, Decimal(50000))], "monthly")
    assert result[0].period == "2026-08"


def test_empty_points_returns_empty_list():
    assert rollups.rollup([], "daily") == []


def test_return_pct_computed_from_open_close():
    points = [_point(0, 0, Decimal(100)), _point(0, 1, Decimal(110))]
    result = rollups.rollup(points, "daily")
    assert result[0].return_pct == Decimal("0.1")
