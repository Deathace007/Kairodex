"""merge option_quotes onto survivor instrument ids

Revision ID: a1b2c3d4e5f6
Revises: f7a1b3c5d8e0
Create Date: 2026-08-19 20:15:00.000000

Split out on purpose from both the small-table repoint (f7a1b3c5d8e0)
before it and the loser-row deletion + guard-rail index after it
(next revision): this is the one genuinely slow step — ~46.8M rows,
several hours — and it should stand alone so a problem in either of
the fast steps around it never again costs a redo of this one. See
f7a1b3c5d8e0's docstring for why that matters (the first, combined
attempt at all of this lost ~4 hours of already-done work here to an
FK violation in the final delete step).

option_quotes is a compressed hypertable. TimescaleDB refuses UPDATE on
a compressed chunk's column outright, and the tuple-decompression cap
(default 100,000/transaction) is far below the ~40M-tuple compressed
backlog here — both had to be worked around on the way to this being
correct: `decompress_chunk` first (no-ops on already-uncompressed
chunks), `SET LOCAL timescaledb.max_tuples_decompressed_per_dml_transaction
= 0` for whatever the decompression step itself still touches.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: str | Sequence[str] | None = "f7a1b3c5d8e0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()

    conn.execute(sa.text("SET LOCAL timescaledb.max_tuples_decompressed_per_dml_transaction = 0"))

    conn.execute(
        sa.text(
            """
            CREATE TEMP TABLE instrument_merge_map ON COMMIT DROP AS
            WITH ranked AS (
                SELECT
                    i.instrument_id,
                    i.exchange,
                    i.provider_ids ->> 'upstox' AS pkey,
                    ROW_NUMBER() OVER (
                        PARTITION BY i.exchange, i.provider_ids ->> 'upstox'
                        ORDER BY i.last_seen DESC, i.instrument_id DESC
                    ) AS rk
                FROM instruments i
                WHERE i.exchange = 'NSE' AND i.provider_ids ? 'upstox'
            )
            SELECT r.instrument_id AS loser_id, s.instrument_id AS survivor_id
            FROM ranked r
            JOIN ranked s
              ON s.exchange = r.exchange AND s.pkey = r.pkey AND s.rk = 1
            WHERE r.rk > 1
            """
        )
    )

    n_pairs = conn.execute(sa.text("SELECT count(*) FROM instrument_merge_map")).scalar_one()
    if n_pairs == 0:
        return

    conn.execute(
        sa.text(
            """
            SELECT decompress_chunk(c)
            FROM show_chunks('option_quotes') c
            """
        )
    )

    conn.execute(
        sa.text(
            """
            DELETE FROM option_quotes child
            USING instrument_merge_map m
            WHERE child.instrument_id = m.loser_id
              AND EXISTS (
                  SELECT 1 FROM option_quotes s2
                  WHERE s2.instrument_id = m.survivor_id AND s2.ts = child.ts
              )
            """
        )
    )
    conn.execute(
        sa.text(
            """
            UPDATE option_quotes child SET instrument_id = m.survivor_id
            FROM instrument_merge_map m
            WHERE child.instrument_id = m.loser_id
            """
        )
    )


def downgrade() -> None:
    # Not reversible — restore option_quotes from the pre-migration backup
    # (see d3e9f1a2b4c6 / the VM's backups/ dir) if this ever needs undoing.
    pass
