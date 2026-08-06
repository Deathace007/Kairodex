from kairodex.export import models as em


def test_trade_export_json_schema_has_expected_properties():
    schema = em.TradeExport.model_json_schema()
    assert "trade_id" in schema["properties"]
    assert "r_multiple" in schema["properties"]


def test_manifest_schema_generates_without_error():
    schema = em.Manifest.model_json_schema()
    assert schema["properties"]["schema_version"]["default"] == "1"


def test_performance_export_round_trips_through_json():
    import json
    from decimal import Decimal

    summary = em.PerformanceSummaryExport(
        n_trades=1, n_open=0, n_closed=1, win_rate=1.0, profit_factor=None,
        expectancy=Decimal(10), avg_r_multiple=1.0, gross_pnl=Decimal(10), net_pnl=Decimal(9),
        total_fees=Decimal(1), avg_win=Decimal(10), avg_loss=None, avg_holding_secs=60.0,
    )
    perf = em.PerformanceExport(
        overall=summary,
        breakdowns={"weekday": {"Monday": summary}},
        equity_curve=em.EquityCurveExport(
            n_points=1, start_equity=Decimal(100), current_equity=Decimal(110),
            high_water_mark=Decimal(110), max_drawdown_pct=Decimal(0),
            total_return_pct=Decimal("0.1"),
        ),
        equity_rollup_daily=[],
    )
    raw = json.loads(perf.model_dump_json())
    assert raw["overall"]["n_trades"] == 1
    assert raw["breakdowns"]["weekday"]["Monday"]["net_pnl"] == "9"  # Decimal -> string

    # the actual round trip: JSON back into a model, and it must equal
    # the original object exactly (not just "some fields look right").
    restored = em.PerformanceExport.model_validate_json(perf.model_dump_json())
    assert restored == perf
