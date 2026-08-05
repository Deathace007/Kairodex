"""backtest_runs

Revision ID: 295470f137eb
Revises: 08bc9b94df88
Create Date: 2026-08-05 20:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '295470f137eb'
down_revision: Union[str, Sequence[str], None] = '08bc9b94df88'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Already exists (354672f0f639) — create_type=False, same reason as every
# other migration touching this enum: a column's enum type independently
# fires CREATE TYPE again via _on_table_create regardless of an earlier
# explicit .create() call.
_segment_enum = postgresql.ENUM(
    'nse_stock', 'nse_index', 'us_stock', 'us_index',
    name='segment_enum', create_type=False,
)


def upgrade() -> None:
    op.create_table(
        'backtest_runs',
        sa.Column('run_id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('segment', _segment_enum, nullable=False),
        sa.Column('strategy_id', sa.BigInteger(), nullable=False),
        sa.Column('from_ts', sa.DateTime(timezone=True), nullable=False),
        sa.Column('to_ts', sa.DateTime(timezone=True), nullable=False),
        sa.Column('config', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('metrics', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('trial_count', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('deflated_sharpe', sa.Numeric(precision=10, scale=6), nullable=True),
        sa.ForeignKeyConstraint(['strategy_id'], ['strategies.strategy_id']),
        sa.PrimaryKeyConstraint('run_id'),
    )
    # trades.run_id existed since trading_tables (c9dcde04143c) as a
    # plain nullable BigInteger, no FK — backtest_runs didn't exist yet
    # to reference. Adding the constraint now; every existing row has
    # run_id IS NULL (live/shadow only so far), so this is safe to add
    # without a data migration.
    op.create_foreign_key(
        'fk_trades_run_id_backtest_runs',
        'trades', 'backtest_runs',
        ['run_id'], ['run_id'],
    )


def downgrade() -> None:
    op.drop_constraint('fk_trades_run_id_backtest_runs', 'trades', type_='foreignkey')
    op.drop_table('backtest_runs')
