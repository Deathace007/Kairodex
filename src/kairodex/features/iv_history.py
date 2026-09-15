"""ATM implied-volatility history for `iv_rank`/`iv_percentile`.

Two halves, split the way the rest of `kairodex.features` is: the DB reads
and writes here, and `with_current` — the pure assembly of the list the
features consume — tested locally.
"""

from __future__ import annotations

import datetime
import statistics
from decimal import Decimal

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from kairodex.core.enums import InstrumentKind, Market
from kairodex.core.sessions import session_window_utc
from kairodex.data.types import ChainSnapshot
from kairodex.store.models import AtmIvDaily, Instrument, OptionQuote

# An IV rank over a handful of sessions is noise dressed as a percentile.
MIN_PRIOR_SESSIONS = 10
MAX_PRIOR_SESSIONS = 252
_ATM_DELTA = (Decimal("0.40"), Decimal("0.60"))
_WINDOW_START = datetime.timedelta(minutes=5 * 60 + 45)  # 15:00 IST = session open + 5h45m
_WINDOW = datetime.timedelta(minutes=15)


def current_atm_iv(chain: list[ChainSnapshot]) -> float | None:
    """Median vendor IV of the nearest expiry's |delta| 0.40-0.60 legs —
    the same definition `record_atm_iv` stores, taken from the live chain."""
    if not chain:
        return None
    front = min(chain, key=lambda s: s.expiry)
    values = [
        float(t.vendor_iv)
        for t in front.quotes
        if t.vendor_iv is not None
        and t.vendor_iv > 0
        and t.delta is not None
        and _ATM_DELTA[0] <= abs(t.delta) <= _ATM_DELTA[1]
    ]
    return statistics.median(values) if values else None


def with_current(
    prior: list[tuple[datetime.datetime, float]],
    current: float | None,
    as_of: datetime.datetime,
    *,
    min_prior: int = MIN_PRIOR_SESSIONS,
) -> list[tuple[datetime.datetime, float]]:
    """`prior` sessions (oldest first) plus the current reading last — the
    shape `iv_rank`/`iv_percentile` read (`values[-1]` is "now"). Empty,
    which makes both features abstain, unless there is a current reading
    and at least `min_prior` sessions to rank it against."""
    if current is None or len(prior) < min_prior:
        return []
    return [*prior, (as_of, current)]


async def load_prior_iv(
    session: AsyncSession, instrument_id: int, before: datetime.date
) -> list[tuple[datetime.datetime, float]]:
    rows = (
        await session.execute(
            select(AtmIvDaily.session_date, AtmIvDaily.atm_iv)
            .where(AtmIvDaily.instrument_id == instrument_id, AtmIvDaily.session_date < before)
            .order_by(AtmIvDaily.session_date.desc())
            .limit(MAX_PRIOR_SESSIONS)
        )
    ).all()
    return [
        (datetime.datetime.combine(d, datetime.time(), tzinfo=datetime.UTC), float(v))
        for d, v in reversed(rows)
    ]


async def record_atm_iv(
    session: AsyncSession, underlying: Instrument, day: datetime.date
) -> Decimal | None:
    """Store `day`'s ATM IV for `underlying`; idempotent (upsert). Returns
    the value, or None if no qualifying quotes existed (holiday, no data).

    Filters `option_quotes` by an explicit instrument-id list and a
    15-minute window so the (instrument_id, ts) primary key does the work —
    joining on `instruments.underlying_symbol` inside the scan took >2 min."""
    ids = list(
        await session.scalars(
            select(Instrument.instrument_id).where(
                Instrument.exchange == underlying.exchange,
                Instrument.underlying_symbol == underlying.symbol,
                Instrument.kind == InstrumentKind.OPTION,
                Instrument.expiry >= day,
            )
        )
    )
    if not ids:
        return None
    market = Market.NSE if underlying.exchange == "NSE" else Market.US
    open_dt, _ = session_window_utc(market, day)
    start = open_dt + _WINDOW_START
    row = (
        await session.execute(
            select(
                func.percentile_cont(0.5).within_group(OptionQuote.vendor_iv),
                func.count(),
            ).where(
                OptionQuote.instrument_id.in_(ids),
                OptionQuote.ts >= start,
                OptionQuote.ts < start + _WINDOW,
                OptionQuote.vendor_iv > 0,
                func.abs(OptionQuote.delta).between(*_ATM_DELTA),
                # WS rows only: REST rows stored vendor_iv in percent before
                # 2026-09-15 (upstox.client.iv_percent_to_fraction). The
                # stream is the consistent fraction series for all history.
                OptionQuote.snapshot_id.is_(None),
            )
        )
    ).one()
    median, n = row
    if median is None or not n:
        # Remove any earlier value for this session, so a re-run after a
        # definition change can't leave a stale row behind.
        await session.execute(
            delete(AtmIvDaily).where(
                AtmIvDaily.instrument_id == underlying.instrument_id,
                AtmIvDaily.session_date == day,
            )
        )
        await session.commit()
        return None
    value = Decimal(str(median)).quantize(Decimal("0.000001"))
    stmt = pg_insert(AtmIvDaily).values(
        instrument_id=underlying.instrument_id, session_date=day, atm_iv=value, n_quotes=n
    )
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=["instrument_id", "session_date"],
            set_={"atm_iv": value, "n_quotes": n},
        )
    )
    await session.commit()
    return value
