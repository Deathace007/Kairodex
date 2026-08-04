"""feed_health

Revision ID: 8ed0be22cd84
Revises: 354672f0f639
Create Date: 2026-08-04 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '8ed0be22cd84'
down_revision: Union[str, Sequence[str], None] = '354672f0f639'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'feed_health',
        sa.Column('provider', sa.String(), nullable=False),
        sa.Column('connected', sa.Boolean(), nullable=False),
        sa.Column('last_message_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_error', sa.String(), nullable=True),
        sa.Column('last_error_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('clock_skew_ms', sa.Integer(), nullable=True),
        sa.Column('quota_used_pct', sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column('subscribed_count', sa.Integer(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('provider'),
    )


def downgrade() -> None:
    op.drop_table('feed_health')
