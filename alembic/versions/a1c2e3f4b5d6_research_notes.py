"""research_notes

Revision ID: a1c2e3f4b5d6
Revises: 295470f137eb
Create Date: 2026-08-06 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'a1c2e3f4b5d6'
down_revision: Union[str, Sequence[str], None] = '295470f137eb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Already exists (354672f0f639) — create_type=False, same reason as every
# other migration touching this enum (see c9dcde04143c's comment).
_segment_enum = postgresql.ENUM(
    'nse_stock', 'nse_index', 'us_stock', 'us_index',
    name='segment_enum', create_type=False,
)


def upgrade() -> None:
    op.create_table(
        'research_notes',
        sa.Column('note_id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('segment', _segment_enum, nullable=True),
        sa.Column('bundle_id', sa.String(), nullable=True),
        sa.Column('source', sa.String(), nullable=False, server_default='claude-code'),
        sa.Column('findings', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('actions', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            'applied_strategy_ids',
            postgresql.ARRAY(sa.BigInteger()),
            nullable=False,
            server_default='{}',
        ),
        sa.Column('status', sa.String(), nullable=False, server_default='open'),
        sa.PrimaryKeyConstraint('note_id'),
    )


def downgrade() -> None:
    op.drop_table('research_notes')
