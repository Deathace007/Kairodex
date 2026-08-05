"""trading_tables

Revision ID: c9dcde04143c
Revises: f1ac2a76a6ba
Create Date: 2026-08-05 15:10:20.649873

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'c9dcde04143c'
down_revision: Union[str, Sequence[str], None] = 'f1ac2a76a6ba'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# segment_enum already exists (354672f0f639). side_enum/strategy_status
# are new here, created explicitly below via .create() before any table
# uses them. create_type=False on ALL three either way: a column's enum
# type independently fires CREATE TYPE again via its own _on_table_create
# dispatch when op.create_table runs, regardless of whether the type was
# already created (explicitly, or by an earlier migration) — caught live
# a second time here, same root cause as feature_vectors' migration.
_segment_enum = postgresql.ENUM(
    'nse_stock', 'nse_index', 'us_stock', 'us_index',
    name='segment_enum', create_type=False,
)
_side_enum = postgresql.ENUM('buy', 'sell', name='side_enum', create_type=False)
_strategy_status_enum = postgresql.ENUM(
    'draft', 'backtested', 'validated', 'shadow', 'paper_small', 'paper_full', 'retired',
    name='strategy_status', create_type=False,
)


def upgrade() -> None:
    _side_enum.create(op.get_bind(), checkfirst=True)
    _strategy_status_enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        'strategies',
        sa.Column('strategy_id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('segment', _segment_enum, nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('params', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('code_sha', sa.String(), nullable=True),
        sa.Column('status', _strategy_status_enum, nullable=False),
        sa.PrimaryKeyConstraint('strategy_id'),
        sa.UniqueConstraint('segment', 'name', 'version', name='uq_strategy_version'),
    )

    op.create_table(
        'strategy_promotions',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('strategy_id', sa.BigInteger(), nullable=False),
        sa.Column('from_status', _strategy_status_enum, nullable=True),
        sa.Column('to_status', _strategy_status_enum, nullable=False),
        sa.Column('at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('approved_by', sa.String(), nullable=False),
        sa.Column('validation_report', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('rationale', sa.String(), nullable=True),
        sa.ForeignKeyConstraint(['strategy_id'], ['strategies.strategy_id']),
        sa.PrimaryKeyConstraint('id'),
    )

    op.create_table(
        'signals',
        sa.Column('signal_id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('ts', sa.DateTime(timezone=True), nullable=False),
        sa.Column('segment', _segment_enum, nullable=False),
        sa.Column('strategy_id', sa.BigInteger(), nullable=False),
        sa.Column('underlying_id', sa.BigInteger(), nullable=False),
        sa.Column('direction', _side_enum, nullable=False),
        sa.Column('confidence', sa.Numeric(precision=6, scale=4), nullable=False),
        # Soft reference, no FK: feature_vectors' PK is the composite
        # (segment, instrument_id, as_of, registry_version) — see
        # FeatureVector's docstring in store/models.py.
        sa.Column('feature_vector_id', sa.BigInteger(), nullable=True),
        sa.Column('evidence', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('decision', sa.String(), nullable=False),
        sa.Column('reject_stage', sa.String(), nullable=True),
        sa.Column('reject_reason', sa.String(), nullable=True),
        sa.Column('forward_outcome', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(['strategy_id'], ['strategies.strategy_id']),
        sa.ForeignKeyConstraint(['underlying_id'], ['instruments.instrument_id']),
        sa.PrimaryKeyConstraint('signal_id'),
    )

    op.create_table(
        'trades',
        sa.Column('trade_id', sa.BigInteger(), autoincrement=True, nullable=False),
        # Soft reference, no FK: backtest_runs doesn't exist yet (P4).
        # NULL = live paper book (paper_trades view below).
        sa.Column('run_id', sa.BigInteger(), nullable=True),
        sa.Column('segment', _segment_enum, nullable=False),
        sa.Column('strategy_id', sa.BigInteger(), nullable=False),
        sa.Column('signal_id', sa.BigInteger(), nullable=False),
        sa.Column('instrument_id', sa.BigInteger(), nullable=False),
        sa.Column('underlying_id', sa.BigInteger(), nullable=False),
        sa.Column('opened_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('qty_lots', sa.Integer(), nullable=False),
        sa.Column('lot_size', sa.Integer(), nullable=False),
        sa.Column('avg_entry', sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column('avg_exit', sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column('premium_paid', sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column('fees', sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column('gross_pnl', sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column('net_pnl', sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column('r_multiple', sa.Numeric(precision=10, scale=4), nullable=True),
        sa.Column('mfe', sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column('mae', sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column('holding_secs', sa.Integer(), nullable=True),
        sa.Column('exit_reason', sa.String(), nullable=True),
        sa.Column('greeks_entry', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('greeks_exit', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('context_entry', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('context_exit', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('risk_params', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('expected_r', sa.Numeric(precision=10, scale=4), nullable=True),
        sa.Column('chart_ref', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(['strategy_id'], ['strategies.strategy_id']),
        sa.ForeignKeyConstraint(['signal_id'], ['signals.signal_id']),
        sa.ForeignKeyConstraint(['instrument_id'], ['instruments.instrument_id']),
        sa.ForeignKeyConstraint(['underlying_id'], ['instruments.instrument_id']),
        sa.PrimaryKeyConstraint('trade_id'),
    )
    op.execute("CREATE VIEW paper_trades AS SELECT * FROM trades WHERE run_id IS NULL")

    op.create_table(
        'trade_events',
        sa.Column('event_id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('trade_id', sa.BigInteger(), nullable=False),
        sa.Column('seq', sa.Integer(), nullable=False),
        sa.Column('ts', sa.DateTime(timezone=True), nullable=False),
        sa.Column('event_type', sa.String(), nullable=False),
        sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('prev_hash', sa.LargeBinary(), nullable=False),
        sa.Column('hash', sa.LargeBinary(), nullable=False),
        sa.ForeignKeyConstraint(['trade_id'], ['trades.trade_id']),
        sa.PrimaryKeyConstraint('event_id'),
        sa.UniqueConstraint('trade_id', 'seq', name='uq_trade_event_seq'),
    )

    # --- Principle 2: the event log is the truth --------------------------
    # ALTER DEFAULT PRIVILEGES (app_role_separation) already granted
    # kairodex_app UPDATE/DELETE on every table including this one — revoke
    # them specifically here, the one deliberate exception. This is only
    # real enforcement because kairodex_app is a non-superuser role (see
    # that migration's docstring); REVOKE against a superuser is a no-op.
    op.execute("REVOKE UPDATE, DELETE ON trade_events FROM kairodex_app")


def downgrade() -> None:
    op.drop_table('trade_events')
    op.execute("DROP VIEW paper_trades")
    op.drop_table('trades')
    op.drop_table('signals')
    op.drop_table('strategy_promotions')
    op.drop_table('strategies')
    _strategy_status_enum.drop(op.get_bind(), checkfirst=True)
    _side_enum.drop(op.get_bind(), checkfirst=True)
