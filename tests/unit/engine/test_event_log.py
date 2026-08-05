"""verify_events is the actual security property (ARCHITECTURE.md
Principle 2) — these tests build event chains by hand and mutate them the
way real tampering would (edit a payload, delete an event, reorder,
splice in a forged one) and confirm every one is caught, not just the
happy path."""

import datetime

from kairodex.engine.event_log import GENESIS_HASH, EventRecord, compute_hash, verify_events

_T0 = datetime.datetime(2026, 8, 5, 9, 15, tzinfo=datetime.UTC)


def _chain(n: int, trade_id: int = 1) -> list[EventRecord]:
    events = []
    prev = GENESIS_HASH
    for seq in range(1, n + 1):
        ts = _T0 + datetime.timedelta(seconds=seq)
        payload = {"step": seq, "note": f"event {seq}"}
        h = compute_hash(trade_id, seq, ts, "SIGNAL_GENERATED", payload, prev)
        events.append(
            EventRecord(
                trade_id=trade_id,
                seq=seq,
                ts=ts,
                event_type="SIGNAL_GENERATED",
                payload=payload,
                prev_hash=prev,
                hash=h,
            )
        )
        prev = h
    return events


def test_compute_hash_is_deterministic():
    h1 = compute_hash(1, 1, _T0, "SIGNAL_GENERATED", {"a": 1}, GENESIS_HASH)
    h2 = compute_hash(1, 1, _T0, "SIGNAL_GENERATED", {"a": 1}, GENESIS_HASH)
    assert h1 == h2
    assert len(h1) == 32  # sha256 digest size


def test_compute_hash_ignores_dict_key_order():
    h1 = compute_hash(1, 1, _T0, "X", {"a": 1, "b": 2}, GENESIS_HASH)
    h2 = compute_hash(1, 1, _T0, "X", {"b": 2, "a": 1}, GENESIS_HASH)
    assert h1 == h2


def test_compute_hash_sensitive_to_every_field():
    base = compute_hash(1, 1, _T0, "X", {"a": 1}, GENESIS_HASH)
    later = _T0 + datetime.timedelta(seconds=1)
    assert compute_hash(2, 1, _T0, "X", {"a": 1}, GENESIS_HASH) != base  # trade_id
    assert compute_hash(1, 2, _T0, "X", {"a": 1}, GENESIS_HASH) != base  # seq
    assert compute_hash(1, 1, later, "X", {"a": 1}, GENESIS_HASH) != base  # ts
    assert compute_hash(1, 1, _T0, "Y", {"a": 1}, GENESIS_HASH) != base  # event_type
    assert compute_hash(1, 1, _T0, "X", {"a": 2}, GENESIS_HASH) != base  # payload
    assert compute_hash(1, 1, _T0, "X", {"a": 1}, b"\x01" * 32) != base  # prev_hash


def test_valid_chain_verifies():
    assert verify_events(_chain(5)) is True


def test_empty_chain_verifies():
    assert verify_events([]) is True


def test_single_event_chain_verifies():
    assert verify_events(_chain(1)) is True


def test_detects_edited_payload():
    events = _chain(5)
    tampered = events[2]
    events[2] = EventRecord(
        trade_id=tampered.trade_id,
        seq=tampered.seq,
        ts=tampered.ts,
        event_type=tampered.event_type,
        payload={"step": 999, "note": "tampered"},  # payload changed, hash not recomputed
        prev_hash=tampered.prev_hash,
        hash=tampered.hash,
    )
    assert verify_events(events) is False


def test_detects_deleted_event():
    events = _chain(5)
    del events[2]  # the next event's prev_hash no longer matches its new predecessor's hash
    assert verify_events(events) is False


def test_detects_reordered_events():
    events = _chain(5)
    events[1], events[2] = events[2], events[1]
    assert verify_events(events) is False


def test_detects_forged_event_with_correct_own_hash_but_broken_link():
    """A forger who recomputes a valid hash for their OWN fake event still
    can't make it link into the existing chain without knowing the real
    next event's prev_hash requirement — inserting it breaks the
    subsequent link."""
    events = _chain(3)
    forged_ts = _T0 + datetime.timedelta(seconds=99)
    forged_payload = {"step": "forged"}
    forged_hash = compute_hash(1, 2, forged_ts, "FORGED", forged_payload, events[0].hash)
    forged = EventRecord(
        trade_id=1,
        seq=2,
        ts=forged_ts,
        event_type="FORGED",
        payload=forged_payload,
        prev_hash=events[0].hash,
        hash=forged_hash,
    )
    # events[2]'s prev_hash still points to the real (not forged) seq-2 hash
    tampered_chain = [events[0], forged, events[2]]
    assert verify_events(tampered_chain) is False


def test_detects_chain_not_starting_from_genesis():
    events = _chain(3)
    events[0] = EventRecord(
        trade_id=events[0].trade_id,
        seq=events[0].seq,
        ts=events[0].ts,
        event_type=events[0].event_type,
        payload=events[0].payload,
        prev_hash=b"\x01" * 32,  # not GENESIS_HASH
        hash=events[0].hash,
    )
    assert verify_events(events) is False
