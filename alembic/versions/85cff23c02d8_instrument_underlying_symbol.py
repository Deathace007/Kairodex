"""instrument underlying_symbol

Revision ID: 85cff23c02d8
Revises: 8ed0be22cd84
Create Date: 2026-08-04 00:00:01.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '85cff23c02d8'
down_revision: Union[str, Sequence[str], None] = '8ed0be22cd84'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('instruments', sa.Column('underlying_symbol', sa.String(), nullable=True))
    op.create_index(
        'ix_instruments_underlying_symbol', 'instruments', ['exchange', 'underlying_symbol']
    )


def downgrade() -> None:
    op.drop_index('ix_instruments_underlying_symbol', table_name='instruments')
    op.drop_column('instruments', 'underlying_symbol')
