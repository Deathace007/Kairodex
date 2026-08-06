import datetime
from decimal import Decimal

from kairodex.analytics.types import EquityCurveStats, PerformanceSummary
from kairodex.core.enums import Segment
from kairodex.export import digest
from kairodex.export.models import DataQualityExport

_FRM = datetime.datetime(2026, 7, 1, tzinfo=datetime.UTC)
_TO = datetime.datetime(2026, 8, 1, tzinfo=datetime.UTC)


def _summary(n_trades: int = 5, net_pnl: Decimal = Decimal(100)) -> PerformanceSummary:
    return PerformanceSummary(
        n_trades=n_trades, n_open=1, n_closed=n_trades - 1,
        win_rate=0.6, profit_factor=1.8, expectancy=Decimal(20),
        avg_r_multiple=0.5, gross_pnl=net_pnl, net_pnl=net_pnl,
        total_fees=Decimal(10), avg_win=Decimal(50), avg_loss=Decimal(-30),
        avg_holding_secs=3600.0,
    )


def _equity_stats() -> EquityCurveStats:
    return EquityCurveStats(
        n_points=10, start_equity=Decimal(50000), current_equity=Decimal(52000),
        high_water_mark=Decimal(53000), max_drawdown_pct=Decimal("0.05"),
        total_return_pct=Decimal("0.04"),
    )


def _data_quality() -> DataQualityExport:
    return DataQualityExport(
        window_from=_FRM, window_to=_TO, gap_rate_by_provider={"upstox": 0.001},
        chain_snapshots_total=100, chain_snapshots_incomplete=2,
        option_quotes_missing_own_greeks=0,
    )


def test_digest_includes_segment_and_window():
    text = digest.build_digest(
        segment=Segment.NSE_STOCK, frm=_FRM, to=_TO, overall=_summary(),
        breakdown_results={}, equity_stats=_equity_stats(), data_quality=_data_quality(),
        n_rejected=42,
    )
    assert "nse_stock" in text
    assert "2026-07-01" in text
    assert "42 signals rejected" in text


def test_digest_renders_breakdown_tables():
    breakdowns = {"weekday": {"Monday": _summary(3), "Tuesday": _summary(2)}}
    text = digest.build_digest(
        segment=Segment.NSE_STOCK, frm=_FRM, to=_TO, overall=_summary(),
        breakdown_results=breakdowns, equity_stats=_equity_stats(), data_quality=_data_quality(),
        n_rejected=0,
    )
    assert "### by weekday" in text
    assert "Monday" in text


def test_digest_skips_empty_breakdown_groups():
    text = digest.build_digest(
        segment=Segment.NSE_STOCK, frm=_FRM, to=_TO, overall=_summary(),
        breakdown_results={"regime": {}}, equity_stats=_equity_stats(),
        data_quality=_data_quality(), n_rejected=0,
    )
    assert "### by regime" not in text


def test_digest_handles_empty_equity_stats():
    empty = EquityCurveStats(
        n_points=0, start_equity=None, current_equity=None,
        high_water_mark=None, max_drawdown_pct=None, total_return_pct=None,
    )
    text = digest.build_digest(
        segment=Segment.US_STOCK, frm=_FRM, to=_TO, overall=_summary(),
        breakdown_results={}, equity_stats=empty, data_quality=_data_quality(),
        n_rejected=0,
    )
    assert "n/a" in text
