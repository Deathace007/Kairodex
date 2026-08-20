"""hard-delete US segment (us_stock/us_index) data, part 2: option_quotes

Revision ID: d7f3c1a9e5b2
Revises: e4b8a2c6d9f1
Create Date: 2026-08-20 12:31:00.000000

Split out on purpose, same reasoning as a1b2c3d4e5f6 (the NSE merge's own
option_quotes step): ~21.8M US rows, the one genuinely slow part of this
removal, isolated so a problem anywhere else never costs redoing this.

option_quotes is a compressed hypertable -- some of the affected chunks
are compressed (verified live: 7 compressed / 6 uncompressed chunks
overall on this table before this migration). `decompress_chunk` first
(no-ops on already-uncompressed chunks, `if_compressed` defaults true --
verified against this install's TimescaleDB 2.28.1 signature, same one
cd123db already corrected for), and
`SET LOCAL timescaledb.max_tuples_decompressed_per_dml_transaction = 0`
so the DELETE itself isn't capped by the default 100,000-tuple limit.
Chunks are not recompressed after -- the existing compression policy job
does that on schedule, same as a1b2c3d4e5f6 left it.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d7f3c1a9e5b2"
down_revision: str | Sequence[str] | None = "e4b8a2c6d9f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()

    conn.execute(sa.text("SET LOCAL timescaledb.max_tuples_decompressed_per_dml_transaction = 0"))
    conn.execute(sa.text("SELECT decompress_chunk(c) FROM show_chunks('option_quotes') c"))

    conn.execute(
        sa.text(
            """
            DELETE FROM option_quotes
            WHERE instrument_id IN (SELECT instrument_id FROM instruments WHERE exchange = 'US')
            """
        )
    )


def downgrade() -> None:
    # Not reversible -- restore from the pre-delete backup
    # (/root/backups/us_segment_removal_2026-08-20/ on the VM) if this
    # ever needs undoing.
    pass
