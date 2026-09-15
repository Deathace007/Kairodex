"""`ingest.record_spec` against a fake session — the SCD-2 decisions only."""

import datetime
from decimal import Decimal

import pytest

from kairodex.core.enums import InstrumentKind
from kairodex.data.ingest import record_spec
from kairodex.data.types import InstrumentRecord
from kairodex.store.models import InstrumentSpec

_TODAY = datetime.date(2026, 9, 15)


class _FakeSession:
    def __init__(self, current: InstrumentSpec | None) -> None:
        self.current = current
        self.added: list[InstrumentSpec] = []

    async def scalar(self, _stmt: object) -> InstrumentSpec | None:
        return self.current

    def add(self, row: InstrumentSpec) -> None:
        self.added.append(row)

    async def flush(self) -> None:
        return None


def _rec(kind: InstrumentKind = InstrumentKind.OPTION, lot: int | None = 75) -> InstrumentRecord:
    return InstrumentRecord(
        exchange="NSE", symbol="NIFTY 23300 PE", kind=kind, currency="INR",
        provider_ids={"upstox": "NSE_FO|1"}, lot_size=lot, tick_size=Decimal("0.05"),
    )


def _spec(lot: int, valid_from: datetime.date) -> InstrumentSpec:
    return InstrumentSpec(
        instrument_id=1, valid_from=valid_from, valid_to=datetime.date.max,
        lot_size=lot, tick_size=Decimal("0.05"),
    )


@pytest.mark.asyncio
async def test_first_sighting_opens_a_row():
    s = _FakeSession(None)
    await record_spec(s, 1, _rec(), _TODAY)  # type: ignore[arg-type]
    assert [(r.lot_size, r.valid_from, r.valid_to) for r in s.added] == [
        (75, _TODAY, datetime.date.max)
    ]


@pytest.mark.asyncio
async def test_unchanged_spec_writes_nothing():
    s = _FakeSession(_spec(75, datetime.date(2026, 9, 1)))
    await record_spec(s, 1, _rec(), _TODAY)  # type: ignore[arg-type]
    assert s.added == []


@pytest.mark.asyncio
async def test_lot_revision_closes_old_row_yesterday_and_opens_today():
    old = _spec(25, datetime.date(2026, 8, 1))
    s = _FakeSession(old)
    await record_spec(s, 1, _rec(lot=75), _TODAY)  # type: ignore[arg-type]
    assert old.valid_to == datetime.date(2026, 9, 14)
    assert [(r.lot_size, r.valid_from) for r in s.added] == [(75, _TODAY)]


@pytest.mark.asyncio
async def test_same_day_correction_updates_in_place():
    today_row = _spec(25, _TODAY)
    s = _FakeSession(today_row)
    await record_spec(s, 1, _rec(lot=75), _TODAY)  # type: ignore[arg-type]
    assert today_row.lot_size == 75 and s.added == []


@pytest.mark.asyncio
async def test_equities_and_missing_lots_are_ignored():
    s = _FakeSession(None)
    await record_spec(s, 1, _rec(kind=InstrumentKind.UNDERLYING), _TODAY)  # type: ignore[arg-type]
    await record_spec(s, 1, _rec(lot=None), _TODAY)  # type: ignore[arg-type]
    assert s.added == []
