"""Append-only, tamper-evident trade event log (ARCHITECTURE.md Principle
2: "the event log is the truth"). Every other trading table is meant to
be a projection rebuildable from this one.

The hash chain is **per trade**, not global: each trade's events form
their own chain from `GENESIS_HASH` at `seq=1`. Concurrent trades never
contend on a shared chain — and per ARCHITECTURE.md §3 a trade only ever
belongs to one segment, and each segment runs in exactly one engine
process, so concurrent *appends to the same trade* shouldn't happen in
practice either.

`verify_events` (pure, DB-free) and `verify_chain`/`append_event`
(DB-touching) are deliberately split — same pattern as
`kairodex.features`' `compute/*.py` vs `loader.py` — so the hash-chain
logic itself is fully testable without a database, and the DB-touching
half stays thin enough to trust once verified live.
"""

from __future__ import annotations

import datetime
import hashlib
import json
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kairodex.store.models import TradeEvent

GENESIS_HASH = b"\x00" * 32


def _canonical_bytes(
    trade_id: int, seq: int, ts: datetime.datetime, event_type: str, payload: dict[str, object]
) -> bytes:
    """A stable serialization of an event's content: same input always
    produces the same bytes. `sort_keys` makes dict key order irrelevant;
    `default=str` handles Decimal/date/etc. values a payload might
    reasonably contain without a bespoke encoder."""
    obj = {
        "trade_id": trade_id,
        "seq": seq,
        "ts": ts.isoformat(),
        "event_type": event_type,
        "payload": payload,
    }
    return json.dumps(obj, sort_keys=True, default=str).encode("utf-8")


def compute_hash(
    trade_id: int,
    seq: int,
    ts: datetime.datetime,
    event_type: str,
    payload: dict[str, object],
    prev_hash: bytes,
) -> bytes:
    body = _canonical_bytes(trade_id, seq, ts, event_type, payload)
    return hashlib.sha256(prev_hash + body).digest()


@dataclass(frozen=True, slots=True)
class EventRecord:
    """The subset of `TradeEvent`'s columns `verify_events` needs —
    decoupled from the ORM row type itself so the verification logic is
    testable with plain fixtures, no DB session."""

    trade_id: int
    seq: int
    ts: datetime.datetime
    event_type: str
    payload: dict[str, object]
    prev_hash: bytes
    hash: bytes


def verify_events(events: list[EventRecord]) -> bool:
    """`events` must already be ordered by `seq` ascending. Returns False
    on the first broken link — a missing/reordered/edited/forged event,
    or a chain that doesn't start from `GENESIS_HASH`."""
    expected_prev = GENESIS_HASH
    for e in events:
        if e.prev_hash != expected_prev:
            return False
        if e.hash != compute_hash(e.trade_id, e.seq, e.ts, e.event_type, e.payload, e.prev_hash):
            return False
        expected_prev = e.hash
    return True


async def append_event(
    session: AsyncSession,
    *,
    trade_id: int,
    event_type: str,
    payload: dict[str, object],
    ts: datetime.datetime | None = None,
) -> TradeEvent:
    """Appends the next event in `trade_id`'s chain, reading the current
    tip (max seq) to compute the next `seq`/`prev_hash`."""
    ts = ts if ts is not None else datetime.datetime.now(datetime.UTC)
    last = await session.scalar(
        select(TradeEvent)
        .where(TradeEvent.trade_id == trade_id)
        .order_by(TradeEvent.seq.desc())
        .limit(1)
    )
    seq = (last.seq + 1) if last is not None else 1
    prev_hash = bytes(last.hash) if last is not None else GENESIS_HASH
    row = TradeEvent(
        trade_id=trade_id,
        seq=seq,
        ts=ts,
        event_type=event_type,
        payload=payload,
        prev_hash=prev_hash,
        hash=compute_hash(trade_id, seq, ts, event_type, payload, prev_hash),
    )
    session.add(row)
    await session.commit()
    return row


async def verify_chain(session: AsyncSession, trade_id: int) -> bool:
    """The REVOKE on UPDATE/DELETE (see the `trading_tables` migration)
    stops the easy tampering path. This is what catches anything that got
    in some other way — e.g. a direct session as the admin `kairodex`
    role, which REVOKE can never stop since it's a superuser."""
    rows = (
        await session.scalars(
            select(TradeEvent).where(TradeEvent.trade_id == trade_id).order_by(TradeEvent.seq)
        )
    ).all()
    events = [
        EventRecord(
            trade_id=r.trade_id,
            seq=r.seq,
            ts=r.ts,
            event_type=r.event_type,
            payload=r.payload,
            prev_hash=bytes(r.prev_hash),
            hash=bytes(r.hash),
        )
        for r in rows
    ]
    return verify_events(events)
