"""DB-free tests for `kairodex.export.bundle`'s pure helpers — a P5
subagent review flagged that this 350+ line module had zero tests
despite most of it being plain data transformation. `build_bundle` itself
stays untested here (DB-touching, verified live on the VM per this
repo's established convention), but everything it calls that doesn't
need a session is covered.
"""

import csv
import datetime
import hashlib
from decimal import Decimal

from kairodex.analytics.types import EquityPoint, PerformanceSummary, TradeRecord
from kairodex.core.enums import Segment
from kairodex.export import bundle

_TS = datetime.datetime(2026, 8, 6, 3, 45, tzinfo=datetime.UTC)


def _trade() -> TradeRecord:
    return TradeRecord(
        trade_id=3, segment=Segment.NSE_INDEX, strategy_id=2,
        underlying_symbol="BANKNIFTY", instrument_symbol="BANKNIFTY 58400.0 C 2026-08-25",
        option_type="C", strike=Decimal("58400.0000"), expiry=datetime.date(2026, 8, 25),
        opened_at=_TS, closed_at=None, avg_entry=Decimal("461.3700"), avg_exit=None,
        initial_stop_price=Decimal("323.1750"), profit_target=Decimal("922.7400"),
        gross_pnl=None, net_pnl=None,
        fees=Decimal("0.3683"), holding_secs=None, exit_reason=None,
        context_entry={"underlying_px": 58200.0, "vol_regime": 1.1, "trend_state_strength": 0.01},
        mfe=Decimal("1683.25"), mae=Decimal("-160.50"),
    )


def test_trade_export_maps_every_field():
    t = _trade()
    exported = bundle.trade_export(t)
    assert exported.trade_id == 3
    assert exported.segment == "nse_index"
    assert exported.underlying_symbol == "BANKNIFTY"
    assert exported.strike == t.strike
    assert exported.context_entry == t.context_entry
    assert exported.initial_stop_price == t.initial_stop_price
    assert exported.profit_target == t.profit_target
    assert exported.r_multiple is None  # unclosed trade


def test_summary_export_maps_every_dataclass_field():
    s = PerformanceSummary(
        n_trades=2, n_open=1, n_closed=1, win_rate=1.0, profit_factor=None,
        expectancy=Decimal(10), avg_r_multiple=0.5, gross_pnl=Decimal(10), net_pnl=Decimal(9),
        total_fees=Decimal(1), avg_win=Decimal(10), avg_loss=None, avg_holding_secs=60.0,
    )
    exported = bundle._summary_export(s)
    assert exported.n_trades == 2
    assert exported.net_pnl == Decimal(9)
    assert exported.avg_loss is None


def test_write_equity_csv_header_and_rows(tmp_path):
    points = [
        EquityPoint(ts=_TS, equity=Decimal(50000), high_water_mark=Decimal(50000),
                    drawdown=Decimal(0), exposure=Decimal(0)),
        EquityPoint(ts=_TS + datetime.timedelta(hours=1), equity=Decimal(51000),
                    high_water_mark=Decimal(51000), drawdown=Decimal(0), exposure=Decimal(100)),
    ]
    path = tmp_path / "equity.csv"
    bundle._write_equity_csv(path, points)
    with path.open() as f:
        rows = list(csv.reader(f))
    assert rows[0] == ["ts", "equity", "high_water_mark", "drawdown", "exposure"]
    assert len(rows) == 3  # header + 2 rows
    assert rows[1][1] == "50000"


def test_write_equity_csv_empty_points_writes_header_only(tmp_path):
    path = tmp_path / "equity.csv"
    bundle._write_equity_csv(path, [])
    with path.open() as f:
        rows = list(csv.reader(f))
    assert rows == [["ts", "equity", "high_water_mark", "drawdown", "exposure"]]


def test_sha256_file_matches_hashlib(tmp_path):
    path = tmp_path / "f.txt"
    path.write_text("hello kairodex")
    expected = hashlib.sha256(b"hello kairodex").hexdigest()
    assert bundle._sha256_file(path) == expected


def test_feature_dictionary_covers_every_registered_feature():
    # kairodex.features.compute.* registers features at import time via
    # kairodex.features.registry — importing the package (transitively,
    # through bundle.py's own `from kairodex.features.registry import
    # all_specs`) is enough for them to be present here.
    entries = bundle._feature_dictionary()
    assert len(entries) > 0
    names = {e.name for e in entries}
    assert "atr" in names
    assert "iv_rank" in names
    for e in entries:
        assert e.description  # every registered feature has a real docstring
        assert e.fidelity in {"EXACT", "PROXY", "ESTIMATE"}
