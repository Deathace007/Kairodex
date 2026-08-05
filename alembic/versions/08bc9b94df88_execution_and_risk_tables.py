"""execution_and_risk_tables

Revision ID: 08bc9b94df88
Revises: c9dcde04143c
Create Date: 2026-08-05 17:36:13.319612

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '08bc9b94df88'
down_revision: Union[str, Sequence[str], None] = 'c9dcde04143c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Both already exist (354672f0f639, f1ac2a76a6ba/c9dcde04143c) —
# create_type=False, the lesson from feature_vectors'/trading_tables'
# migrations (a column's enum type independently fires CREATE TYPE again
# via _on_table_create regardless of an earlier explicit .create() call).
_segment_enum = postgresql.ENUM(
    'nse_stock', 'nse_index', 'us_stock', 'us_index',
    name='segment_enum', create_type=False,
)
_side_enum = postgresql.ENUM('buy', 'sell', name='side_enum', create_type=False)


def upgrade() -> None:
    op.create_table(
        'orders',
        sa.Column('order_id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('trade_id', sa.BigInteger(), nullable=False),
        sa.Column('ts', sa.DateTime(timezone=True), nullable=False),
        sa.Column('instrument_id', sa.BigInteger(), nullable=False),
        sa.Column('side', _side_enum, nullable=False),
        sa.Column('qty', sa.Integer(), nullable=False),
        sa.Column('order_type', sa.String(), nullable=False),
        sa.Column('limit_price', sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column('status', sa.String(), nullable=False),
        sa.Column('sim_params', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(['trade_id'], ['trades.trade_id']),
        sa.ForeignKeyConstraint(['instrument_id'], ['instruments.instrument_id']),
        sa.PrimaryKeyConstraint('order_id'),
    )

    op.create_table(
        'fills',
        sa.Column('fill_id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('order_id', sa.BigInteger(), nullable=False),
        sa.Column('ts', sa.DateTime(timezone=True), nullable=False),
        sa.Column('qty', sa.Integer(), nullable=False),
        sa.Column('price', sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column('spread_bps', sa.Numeric(precision=10, scale=4), nullable=True),
        sa.Column('slippage_bps', sa.Numeric(precision=10, scale=4), nullable=True),
        sa.Column('fill_model', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(['order_id'], ['orders.order_id']),
        sa.PrimaryKeyConstraint('fill_id'),
    )

    op.create_table(
        'position_marks',
        sa.Column('trade_id', sa.BigInteger(), nullable=False),
        sa.Column('ts', sa.DateTime(timezone=True), nullable=False),
        sa.Column('mark', sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column('unrealized', sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column('underlying_px', sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column('greeks', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(['trade_id'], ['trades.trade_id']),
        sa.PrimaryKeyConstraint('trade_id', 'ts'),
    )
    op.execute(
        "SELECT create_hypertable('position_marks', 'ts', "
        "chunk_time_interval => interval '1 day')"
    )

    op.create_table(
        'equity_snapshots',
        sa.Column('segment', _segment_enum, nullable=False),
        sa.Column('ts', sa.DateTime(timezone=True), nullable=False),
        sa.Column('run_id', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('cash', sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column('unrealized', sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column('realized', sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column('equity', sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column('exposure', sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column('utilization', sa.Numeric(precision=8, scale=4), nullable=False),
        sa.Column('high_water_mark', sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column('drawdown', sa.Numeric(precision=8, scale=4), nullable=False),
        sa.PrimaryKeyConstraint('segment', 'ts', 'run_id'),
    )
    op.execute(
        "SELECT create_hypertable('equity_snapshots', 'ts', "
        "chunk_time_interval => interval '7 days')"
    )

    op.create_table(
        'risk_state',
        sa.Column('segment', _segment_enum, nullable=False),
        sa.Column('as_of', sa.DateTime(timezone=True), nullable=False),
        sa.Column('daily_pnl', sa.Numeric(precision=18, scale=4), nullable=False, server_default='0'),
        sa.Column('weekly_pnl', sa.Numeric(precision=18, scale=4), nullable=False, server_default='0'),
        sa.Column('consecutive_losses', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('breaker_status', sa.String(), nullable=False, server_default='ARMED'),
        sa.Column('breaker_reason', sa.String(), nullable=True),
        sa.Column('blocked_until', sa.DateTime(timezone=True), nullable=True),
        sa.Column('risk_multiplier', sa.Numeric(precision=6, scale=4), nullable=False, server_default='1'),
        sa.PrimaryKeyConstraint('segment'),
    )


def downgrade() -> None:
    op.drop_table('risk_state')
    op.drop_table('equity_snapshots')
    op.drop_table('position_marks')
    op.drop_table('fills')
    op.drop_table('orders')
