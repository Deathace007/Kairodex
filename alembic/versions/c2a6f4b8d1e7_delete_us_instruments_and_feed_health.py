"""hard-delete US segment (us_stock/us_index) data, part 3: instruments
and the lse feed_health row

Revision ID: c2a6f4b8d1e7
Revises: d7f3c1a9e5b2
Create Date: 2026-08-20 12:32:00.000000

Last of the three-part US segment removal (e4b8a2c6d9f1 deleted every
other FK child, d7f3c1a9e5b2 deleted option_quotes). By now every
exchange='US' instrument row should be childless -- but this delete is
deliberately defensive rather than trusting that, same pattern as
b6f5e4d3c2a1's loser-instrument delete: only removes a row once
`NOT EXISTS` confirms nothing still references it across every table with
a FK to `instruments` (including the self-referential `underlying_id`).
A row that still has an unexpected reference is left in place and
reported via `RAISE NOTICE` instead of aborting the whole migration on a
`ForeignKeyViolation`.

Then `feed_health`'s 'lse' row (PK = provider) -- the last piece of
US-segment state anywhere in the schema.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c2a6f4b8d1e7"
down_revision: str | Sequence[str] | None = "d7f3c1a9e5b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Every table with a FK into instruments(instrument_id), including the
# self-referential one, for the NOT EXISTS childless check below.
_CHILD_TABLES = [
    ("instruments", "underlying_id"),
    ("instrument_specs", "instrument_id"),
    ("watchlist_membership", "instrument_id"),
    ("corporate_actions", "instrument_id"),
    ("underlying_bars", "instrument_id"),
    ("chain_snapshots", "underlying_id"),
    ("option_quotes", "instrument_id"),
    ("market_depth", "instrument_id"),
    ("options_flow", "instrument_id"),
    ("feature_vectors", "instrument_id"),
    ("signals", "underlying_id"),
    ("trades", "instrument_id"),
    ("trades", "underlying_id"),
    ("orders", "instrument_id"),
]


def upgrade() -> None:
    conn = op.get_bind()

    not_referenced = " AND ".join(
        f"NOT EXISTS (SELECT 1 FROM {table} c WHERE c.{col} = i.instrument_id)"
        for table, col in _CHILD_TABLES
    )
    result = conn.execute(
        sa.text(
            f"""
            DELETE FROM instruments i
            WHERE i.exchange = 'US'
              AND {not_referenced}
            """
        )
    )
    deleted = result.rowcount

    skipped = conn.execute(
        sa.text("SELECT count(*) FROM instruments WHERE exchange = 'US'")
    ).scalar_one()
    if skipped:
        notice = (
            f"{skipped} US instrument row(s) still referenced somewhere and were left in "
            f"place (deleted {deleted}) -- investigate before assuming the removal is complete"
        )
        conn.execute(sa.text(f"DO $$ BEGIN RAISE NOTICE '{notice}'; END $$;"))

    conn.execute(sa.text("DELETE FROM feed_health WHERE provider = 'lse'"))


def downgrade() -> None:
    # Not reversible -- restore from the pre-delete backup
    # (/root/backups/us_segment_removal_2026-08-20/ on the VM) if this
    # ever needs undoing.
    pass
