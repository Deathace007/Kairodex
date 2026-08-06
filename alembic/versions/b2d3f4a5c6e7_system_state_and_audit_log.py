"""system_state_and_audit_log

Revision ID: b2d3f4a5c6e7
Revises: a1c2e3f4b5d6
Create Date: 2026-08-06 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'b2d3f4a5c6e7'
down_revision: Union[str, Sequence[str], None] = 'a1c2e3f4b5d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'system_state',
        sa.Column('id', sa.SmallInteger(), nullable=False, server_default='1'),
        sa.Column('kill_engaged', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('kill_reason', sa.String(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint('id = 1', name='ck_system_state_singleton'),
        sa.PrimaryKeyConstraint('id'),
    )
    # Seed the one row up front — build_account_state (risk/loader.py)
    # does a plain session.get(SystemState, 1) with no "row missing"
    # fallback path, same convention as risk_state's per-segment rows
    # (P3), which are always pre-existing by the time anything reads them.
    op.execute(
        "INSERT INTO system_state (id, kill_engaged, updated_at) "
        "VALUES (1, false, now())"
    )

    op.create_table(
        'audit_log',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('ts', sa.DateTime(timezone=True), nullable=False),
        sa.Column('actor', sa.String(), nullable=False),
        sa.Column('action', sa.String(), nullable=False),
        sa.Column('target', sa.String(), nullable=False),
        sa.Column('before', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('after', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    op.drop_table('audit_log')
    op.drop_table('system_state')
