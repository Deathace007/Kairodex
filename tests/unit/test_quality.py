import datetime
from decimal import Decimal

from kairodex.data.quality import QualityFlag, flag_tick
from kairodex.data.types import Tick

_NOW = datetime.datetime(2026, 8, 4, 10, 0, 0, tzinfo=datetime.UTC)


def _tick(**overrides: object) -> Tick:
    defaults: dict[str, object] = dict(instrument_key="X", ts=_NOW)
    defaults.update(overrides)
    return Tick(**defaults)  # type: ignore[arg-type]


def test_clean_tick_has_no_flags():
    assert flag_tick(_tick(ltp=Decimal(100)), now=_NOW) == QualityFlag.NONE


def test_stale_tick_flagged():
    old = _tick(ts=_NOW - datetime.timedelta(seconds=30))
    assert flag_tick(old, now=_NOW) & QualityFlag.STALE


def test_crossed_book_flagged():
    t = _tick(bid=Decimal(101), ask=Decimal(100))
    assert flag_tick(t, now=_NOW) & QualityFlag.CROSSED_BOOK


def test_zero_volume_flagged():
    t = _tick(volume=0)
    assert flag_tick(t, now=_NOW) & QualityFlag.ZERO_VOLUME


def test_price_outlier_flagged():
    prev = _tick(ltp=Decimal(100))
    curr = _tick(ltp=Decimal(200))
    assert flag_tick(curr, now=_NOW, prev_tick=prev) & QualityFlag.OUTLIER


def test_sequence_gap_flagged():
    prev = _tick(ts=_NOW - datetime.timedelta(seconds=120))
    assert flag_tick(
        prev_tick=prev,
        tick=_tick(ts=_NOW),
        now=_NOW,
        expected_interval=datetime.timedelta(seconds=5),
    ) & QualityFlag.SEQUENCE_GAP


def test_no_sequence_gap_within_expected_cadence():
    prev = _tick(ts=_NOW - datetime.timedelta(seconds=5))
    flags = flag_tick(
        prev_tick=prev,
        tick=_tick(ts=_NOW),
        now=_NOW,
        expected_interval=datetime.timedelta(seconds=5),
    )
    assert not flags & QualityFlag.SEQUENCE_GAP
