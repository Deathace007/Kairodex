"""feature_vectors

Revision ID: 5256d032cc30
Revises: 85cff23c02d8
Create Date: 2026-08-05 13:49:24.939324

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '5256d032cc30'
down_revision: Union[str, Sequence[str], None] = '85cff23c02d8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'feature_vectors',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            'segment',
            # segment_enum already exists (created by 354672f0f639).
            # create_type=False on the generic sa.Enum did NOT stop this
            # from re-issuing CREATE TYPE (caught live on the VM a second
            # time): sa.Enum gets adapted into a dialect-specific impl at
            # DDL-compile time, and that adaptation doesn't reliably carry
            # create_type through — postgresql.ENUM directly does.
            postgresql.ENUM(
                'nse_stock', 'nse_index', 'us_stock', 'us_index',
                name='segment_enum', create_type=False,
            ),
            nullable=False,
        ),
        sa.Column('instrument_id', sa.BigInteger(), nullable=False),
        sa.Column('as_of', sa.DateTime(timezone=True), nullable=False),
        sa.Column('event_ts', sa.DateTime(timezone=True), nullable=False),
        sa.Column('registry_version', sa.String(), nullable=False),
        sa.Column('values', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('quality', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(['instrument_id'], ['instruments.instrument_id']),
        sa.PrimaryKeyConstraint('segment', 'instrument_id', 'as_of', 'registry_version'),
    )

    # --- Hypertable conversion -----------------------------------------
    # Not expressible via SQLAlchemy's DDL — see ARCHITECTURE.md §5.3.
    # `id bigserial PRIMARY KEY` from the doc's literal SQL is dropped in
    # favor of the composite PK above: Timescale requires every unique
    # index on a hypertable to include the partitioning column (`as_of`),
    # which a lone `id` PK doesn't — `create_hypertable` would reject it.
    # See FeatureVector's docstring in store/models.py for the full
    # reasoning and what a future FK to this table needs to account for.
    op.execute(
        "SELECT create_hypertable('feature_vectors', 'as_of', "
        "chunk_time_interval => interval '7 days')"
    )


def downgrade() -> None:
    op.drop_table('feature_vectors')
