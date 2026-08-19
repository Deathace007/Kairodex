"""repoint remaining NSE instrument-merge FK stragglers

Revision ID: f7a1b3c5d8e0
Revises: d3e9f1a2b4c6
Create Date: 2026-08-19 15:45:00.000000

`d3e9f1a2b4c6` repointed every small FK table for the NSE duplicate
merge, but a follow-up check (2026-08-19 20:08 IST, after the
option_quotes-merge migration failed on this) found `underlying_bars`
still had 1,437 rows on loser instrument_id=1 ("Nifty 50", the dead twin
of the live "NIFTY" row, instrument_id=6031, watchlist_membership's
actual underlying) — the fix that migration made to the OTHER keyed
tables evidently didn't fully land for this one row/table combination,
root cause not chased further since re-running the identical,
already-proven-correct repoint logic is both the fix and the diagnostic:
if any table still has stragglers, this sweep catches them too.

This is deliberately split out from the option_quotes merge (a separate,
much slower migration) rather than folded back into one transaction: the
first attempt at that combined migration ran for ~4 hours, successfully
rewrote all 46.8M option_quotes rows, and then failed at the very last
step (deleting loser instruments rows) on exactly this FK straggler —
throwing away all 4 hours of work when the transaction rolled back. Small,
fast, and isolated is the point: if this migration or a future one still
misses something, only the fast part needs re-running, not the slow one.

Idempotent: re-running the same collision-safe repoint against tables
that are already fully merged is a set of no-op UPDATEs.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f7a1b3c5d8e0"
down_revision: str | Sequence[str] | None = "d3e9f1a2b4c6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SIMPLE_TABLES = [
    ("trades", "instrument_id"),
    ("trades", "underlying_id"),
    ("orders", "instrument_id"),
    ("signals", "underlying_id"),
    ("chain_snapshots", "underlying_id"),
    ("instruments", "underlying_id"),
]

_KEYED_TABLES = [
    ("underlying_bars", "instrument_id", ["timeframe", "ts"]),
    ("market_depth", "instrument_id", ["ts", "side", "level"]),
    ("options_flow", "instrument_id", ["ts", "price", "size"]),
    ("corporate_actions", "instrument_id", ["ex_date", "action_type"]),
    ("instrument_specs", "instrument_id", ["valid_from"]),
    ("watchlist_membership", "instrument_id", ["segment", "valid_from"]),
    ("feature_vectors", "instrument_id", ["segment", "as_of", "registry_version"]),
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

    n_pairs = conn.execute(sa.text("SELECT count(*) FROM instrument_merge_map")).scalar_one()
    if n_pairs == 0:
        return

    for table, col, keys in _KEYED_TABLES:
        key_match = " AND ".join(f"s2.{k} = child.{k}" for k in keys)
        conn.execute(
            sa.text(
                f"""
                DELETE FROM {table} child
                USING instrument_merge_map m
                WHERE child.{col} = m.loser_id
                  AND EXISTS (
                      SELECT 1 FROM {table} s2
                      WHERE s2.{col} = m.survivor_id AND {key_match}
                  )
                """
            )
        )
        conn.execute(
            sa.text(
                f"""
                UPDATE {table} child SET {col} = m.survivor_id
                FROM instrument_merge_map m
                WHERE child.{col} = m.loser_id
                """
            )
        )

    for table, col in _SIMPLE_TABLES:
        conn.execute(
            sa.text(
                f"""
                UPDATE {table} child SET {col} = m.survivor_id
                FROM instrument_merge_map m
                WHERE child.{col} = m.loser_id
                """
            )
        )


def downgrade() -> None:
    # Not reversible, and nothing here is destructive to reverse — this
    # only repoints FK columns that were already meant to point at the
    # survivor per d3e9f1a2b4c6.
    pass
