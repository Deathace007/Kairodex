"""ORM models for ARCHITECTURE.md §5.1 (reference), §5.2 (market data), and
§5.3 (features — P2's `FeatureVector`, added once P2's registry actually
reads and writes it).

Scope note: §5.4 trading, §5.5 research/ops still land with the phases
that actually read and write them (P3/P5) — declaring empty tables now
would be schema with no consumer.

Hypertable conversion, compression, and retention policies are not
expressible in the ORM; they're applied as raw SQL in the Alembic migration
that creates these tables (alembic/versions/0001_reference_and_market_data.py).
"""

from __future__ import annotations

import datetime
import uuid
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    ForeignKey,
    Identity,
    Numeric,
    SmallInteger,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import BigInteger, Boolean, DateTime, Enum, Integer

from kairodex.core.enums import InstrumentKind, Segment
from kairodex.store.base import Base

_segment_enum = Enum(Segment, name="segment_enum", values_callable=lambda e: [m.value for m in e])
_kind_enum = Enum(
    InstrumentKind, name="instrument_kind", values_callable=lambda e: [m.value for m in e]
)


class Instrument(Base):
    __tablename__ = "instruments"
    __table_args__ = (
        UniqueConstraint(
            "exchange", "symbol", "expiry", "strike", "option_type", name="uq_instrument_identity"
        ),
    )

    instrument_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    segment: Mapped[Segment | None] = mapped_column(_segment_enum)
    kind: Mapped[InstrumentKind] = mapped_column(_kind_enum, nullable=False)
    symbol: Mapped[str] = mapped_column(String, nullable=False)
    exchange: Mapped[str] = mapped_column(String, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    underlying_id: Mapped[int | None] = mapped_column(ForeignKey("instruments.instrument_id"))
    # Plain string, not just the underlying_id FK: an option's underlying_id
    # can only be backfilled once the underlying's own row exists, but the
    # vendor instrument dump doesn't guarantee that ordering. This column is
    # always known from the option's own record, so T1 chain polling
    # (kairodex.data.recorder) can resolve "which expiries exist for
    # RELIANCE" without depending on ingestion order.
    underlying_symbol: Mapped[str | None] = mapped_column(String)
    strike: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    option_type: Mapped[str | None] = mapped_column(String(1))  # C|P — core.enums.OptionType
    expiry: Mapped[datetime.date | None] = mapped_column(Date)
    exercise_style: Mapped[str | None] = mapped_column(String)
    settlement: Mapped[str | None] = mapped_column(String)
    provider_ids: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    first_seen: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class InstrumentSpec(Base):
    """SCD-2: the lot/tick size in effect at a point in time (SPEC_REVIEW.md
    C5 — NIFTY's lot went 25 -> 75 mid-history; a backtest must use the one
    that was actually in force)."""

    __tablename__ = "instrument_specs"

    instrument_id: Mapped[int] = mapped_column(
        ForeignKey("instruments.instrument_id"), primary_key=True
    )
    valid_from: Mapped[datetime.date] = mapped_column(Date, primary_key=True)
    valid_to: Mapped[datetime.date] = mapped_column(Date, nullable=False, default=datetime.date.max)
    lot_size: Mapped[int] = mapped_column(Integer, nullable=False)
    tick_size: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)


class TradingCalendar(Base):
    __tablename__ = "trading_calendar"

    exchange: Mapped[str] = mapped_column(String, primary_key=True)
    session_date: Mapped[datetime.date] = mapped_column(Date, primary_key=True)
    is_holiday: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    open_utc: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    close_utc: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    tz: Mapped[str] = mapped_column(String, nullable=False)


class WatchlistMembership(Base):
    """Point-in-time membership — kills survivorship bias (SPEC_REVIEW.md C5)."""

    __tablename__ = "watchlist_membership"

    segment: Mapped[Segment] = mapped_column(_segment_enum, primary_key=True)
    instrument_id: Mapped[int] = mapped_column(
        ForeignKey("instruments.instrument_id"), primary_key=True
    )
    valid_from: Mapped[datetime.date] = mapped_column(Date, primary_key=True)
    valid_to: Mapped[datetime.date] = mapped_column(Date, nullable=False, default=datetime.date.max)
    tier: Mapped[int] = mapped_column(SmallInteger, nullable=False)  # 1=T1, 2=T2-eligible


class CorporateAction(Base):
    __tablename__ = "corporate_actions"

    instrument_id: Mapped[int] = mapped_column(
        ForeignKey("instruments.instrument_id"), primary_key=True
    )
    ex_date: Mapped[datetime.date] = mapped_column(Date, primary_key=True)
    action_type: Mapped[str] = mapped_column(String, primary_key=True)
    ratio: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    adj_factor: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    source: Mapped[str | None] = mapped_column(String)


class FxRate(Base):
    __tablename__ = "fx_rates"

    as_of: Mapped[datetime.date] = mapped_column(Date, primary_key=True)
    pair: Mapped[str] = mapped_column(String, primary_key=True)
    rate: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)


# --- §5.2 Market data (hypertables) -----------------------------------------


class UnderlyingBar(Base):
    __tablename__ = "underlying_bars"

    instrument_id: Mapped[int] = mapped_column(
        ForeignKey("instruments.instrument_id"), primary_key=True
    )
    timeframe: Mapped[str] = mapped_column(String, primary_key=True)
    ts: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    open: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    high: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    low: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    close: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    volume: Mapped[int] = mapped_column(BigInteger, nullable=False)
    vwap: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    source: Mapped[str] = mapped_column(String, nullable=False)
    quality: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)


class ChainSnapshot(Base):
    """Completeness metadata for one atomic chain read — an incomplete chain
    is unusable for signals but is still stored (ARCHITECTURE.md §7)."""

    __tablename__ = "chain_snapshots"

    snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    underlying_id: Mapped[int] = mapped_column(ForeignKey("instruments.instrument_id"))
    ts: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    contract_count: Mapped[int] = mapped_column(Integer, nullable=False)
    expected_count: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    complete: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class OptionQuote(Base):
    __tablename__ = "option_quotes"

    instrument_id: Mapped[int] = mapped_column(
        ForeignKey("instruments.instrument_id"), primary_key=True
    )
    ts: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    snapshot_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    bid: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    ask: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    bid_sz: Mapped[int | None] = mapped_column(Integer)
    ask_sz: Mapped[int | None] = mapped_column(Integer)
    ltp: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    volume: Mapped[int | None] = mapped_column(BigInteger)
    oi: Mapped[int | None] = mapped_column(BigInteger)
    oi_change: Mapped[int | None] = mapped_column(BigInteger)
    underlying_px: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    iv: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    delta: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    gamma: Mapped[Decimal | None] = mapped_column(Numeric(14, 10))
    theta: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    vega: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    rho: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    greeks_model: Mapped[str | None] = mapped_column(String)
    greeks_inputs: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    vendor_iv: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))  # cross-check only
    tier: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)
    source: Mapped[str] = mapped_column(String, nullable=False)
    quality: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)


class MarketDepth(Base):
    """T2 only, 30-day retention, no exception (ARCHITECTURE.md §6)."""

    __tablename__ = "market_depth"
    __table_args__ = (CheckConstraint("side IN ('B','S')", name="ck_depth_side"),)

    instrument_id: Mapped[int] = mapped_column(
        ForeignKey("instruments.instrument_id"), primary_key=True
    )
    ts: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    side: Mapped[str] = mapped_column(String(1), primary_key=True)
    level: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    price: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    qty: Mapped[int] = mapped_column(Integer, nullable=False)
    orders: Mapped[int | None] = mapped_column(Integer)


class FeedHealth(Base):
    """One row per vendor connection — P1's feed-health tracking
    (ARCHITECTURE.md §7, §17). Per-instrument gap/staleness is derived from
    `option_quotes`/`underlying_bars` at query time (their `quality` bitmask
    and `max(ts)`), not duplicated here — this table is only the thing a
    per-instrument query can't tell you: is the socket itself up."""

    __tablename__ = "feed_health"

    provider: Mapped[str] = mapped_column(String, primary_key=True)
    connected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_message_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String)
    last_error_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    clock_skew_ms: Mapped[int | None] = mapped_column(Integer)
    quota_used_pct: Mapped[float | None] = mapped_column(Numeric(5, 2))
    subscribed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class OptionsFlow(Base):
    """US only — LSE options-flow prints (ARCHITECTURE.md §5.2)."""

    __tablename__ = "options_flow"

    instrument_id: Mapped[int] = mapped_column(
        ForeignKey("instruments.instrument_id"), primary_key=True
    )
    ts: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    price: Mapped[Decimal] = mapped_column(Numeric(18, 4), primary_key=True)
    size: Mapped[int] = mapped_column(Integer, primary_key=True)
    premium: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    aggressor: Mapped[str | None] = mapped_column(String(1))
    greeks_at_print: Mapped[dict[str, object] | None] = mapped_column(JSONB)


class FeatureVector(Base):
    """ARCHITECTURE.md §5.3 — one row per (segment, instrument, as_of,
    registry_version), every registered feature's value/quality bundled
    into the two JSONB columns rather than one row per feature: "the
    feature set will churn weekly during research" is exactly why jsonb
    was the spec's own call here, not a P2 shortcut.

    `id bigserial` is a convenience row identifier, **not** a primary key
    or otherwise globally-unique column — deliberately, deviating from
    the literal SQL in ARCHITECTURE.md §5.3. TimescaleDB requires every
    unique index on a hypertable to include the partitioning column
    (`as_of`); `id bigserial PRIMARY KEY` alone doesn't, and
    `create_hypertable` rejects it. The composite below both satisfies
    that constraint and *is* the spec's stated `UNIQUE(...)` intent — see
    the migration for the real DDL. If a future `signals.feature_vector_id`
    FK (§5.4, P3) needs to reference a row, it'll need to carry `as_of`
    alongside `id` and FK on the composite — the standard Timescale
    pattern for this, not solved here since nothing consumes it yet.

    `id` needs `Identity()`, not just `autoincrement=True` — caught live:
    since `id` isn't the (sole) primary key here, plain `autoincrement`
    doesn't get SQLAlchemy/Postgres to attach a sequence the way a
    single-column integer PK would; without `Identity()` the column has
    no default at all and every insert omitting `id` violates NOT NULL.
    """

    __tablename__ = "feature_vectors"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), nullable=False)
    segment: Mapped[Segment] = mapped_column(_segment_enum, primary_key=True)
    instrument_id: Mapped[int] = mapped_column(
        ForeignKey("instruments.instrument_id"), primary_key=True
    )
    as_of: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    event_ts: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    registry_version: Mapped[str] = mapped_column(String, primary_key=True)
    values: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    quality: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
