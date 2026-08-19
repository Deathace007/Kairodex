"""delete loser NSE instrument rows, add duplicate-identity guard rail

Revision ID: b6f5e4d3c2a1
Revises: a1b2c3d4e5f6
Create Date: 2026-08-19 20:20:00.000000

Last of the three-part NSE instrument merge (f7a1b3c5d8e0 repointed the
small FK tables, a1b2c3d4e5f6 merged option_quotes). By the time this
runs every loser instrument_id should be childless — but the delete
below is deliberately defensive rather than trusting that: it only
deletes a loser row once `NOT EXISTS` confirms nothing still references
it, across every table with a FK to `instruments`. A row that still has
an unexpected reference is left in place and reported via `RAISE NOTICE`
instead of raising a `ForeignKeyViolation` that would abort this whole
(fast) migration — that all-or-nothing failure mode is exactly what cost
~4 hours of redone work the first time this merge was attempted as one
combined transaction (see f7a1b3c5d8e0's docstring).

The guard-rail unique indexes are NOT defensive the same way — if any
duplicate group still exists when `CREATE UNIQUE INDEX` runs, it should
fail loudly. That would mean something is still wrong with the merge and
deserves investigation, not a silent skip.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b6f5e4d3c2a1"
down_revision: str | Sequence[str] | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Every table with a FK into instruments(instrument_id), for the
# NOT EXISTS childless check below.
_CHILD_TABLES = [
    ("trades", "instrument_id"),
    ("trades", "underlying_id"),
    ("orders", "instrument_id"),
    ("signals", "underlying_id"),
    ("chain_snapshots", "underlying_id"),
    ("instruments", "underlying_id"),
    ("underlying_bars", "instrument_id"),
    ("market_depth", "instrument_id"),
    ("options_flow", "instrument_id"),
    ("corporate_actions", "instrument_id"),
    ("instrument_specs", "instrument_id"),
    ("watchlist_membership", "instrument_id"),
    ("feature_vectors", "instrument_id"),
    ("option_quotes", "instrument_id"),
]


def upgrade() -> None:
    conn = op.get_bind()

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

    not_referenced = " AND ".join(
        f"NOT EXISTS (SELECT 1 FROM {table} c WHERE c.{col} = i.instrument_id)"
        for table, col in _CHILD_TABLES
    )
    result = conn.execute(
        sa.text(
            f"""
            DELETE FROM instruments i
            WHERE i.instrument_id IN (SELECT loser_id FROM instrument_merge_map)
              AND {not_referenced}
            """
        )
    )
    deleted = result.rowcount

    skipped = conn.execute(
        sa.text(
            """
            SELECT count(*) FROM instrument_merge_map m
            WHERE EXISTS (SELECT 1 FROM instruments i WHERE i.instrument_id = m.loser_id)
            """
        )
    ).scalar_one()
    if skipped:
        notice = (
            f"{skipped} loser instrument row(s) still referenced somewhere and were left "
            f"in place (deleted {deleted}) -- investigate before assuming the merge is complete"
        )
        conn.execute(sa.text(f"DO $$ BEGIN RAISE NOTICE '{notice}'; END $$;"))

    op.create_index(
        "uq_instruments_provider_upstox",
        "instruments",
        [sa.text("exchange"), sa.text("(provider_ids ->> 'upstox')")],
        unique=True,
        postgresql_where=sa.text("provider_ids ? 'upstox'"),
    )
    op.create_index(
        "uq_instruments_provider_lse",
        "instruments",
        [sa.text("exchange"), sa.text("(provider_ids ->> 'lse')")],
        unique=True,
        postgresql_where=sa.text("provider_ids ? 'lse'"),
    )


def downgrade() -> None:
    op.drop_index("uq_instruments_provider_lse", table_name="instruments")
    op.drop_index("uq_instruments_provider_upstox", table_name="instruments")
    # Deleted loser rows are not restorable here — restore instruments
    # from the pre-migration backup if this ever needs undoing.
