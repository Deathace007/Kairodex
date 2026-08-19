"""merge NSE instrument duplicate identities (small tables only)

Revision ID: d3e9f1a2b4c6
Revises: b2d3f4a5c6e7
Create Date: 2026-08-19 00:00:00.000000

Two `instruments` rows exist per real NSE option contract wherever
`sync-instruments` (Upstox trading_symbol, e.g. "SBIN 1060 PE 25 AUG 26")
and `store_chain_snapshot` (synthesized, e.g. "SBIN 1060.0 P 2026-08-25")
both ran before 2026-08-10's `upsert_instrument` fix started matching on
provider key. That fix stops *new* duplicates; it never merged the 3,424
pairs already on disk. Both rows in a pair carry the identical
`provider_ids['upstox']` value, so nothing but an unordered
`session.scalar()` pick (now fixed — see `ingest.upsert_instrument`)
decided which row a given ingest write landed on. That let the quote feed
silently move, session to session, from the row an open trade is pinned
to onto its twin — orphaning the position's price feed. SBIN (trade 95)
and TCS (trade 101) sat on a dead row for 23+ hours on 2026-08-18/19
before this was caught. See docs/PROGRESS.md §15 and the 2026-08-19 entry.

For every (exchange='NSE', provider_ids->>'upstox') group with more than
one row, this repoints every *small* FK table at the survivor (the row
with the most recent `last_seen`, i.e. the one still being written to).

Deliberately excluded: `option_quotes`. 46.8M of its ~102.6M rows belong
to loser instrument_ids — rewriting that live, during market hours, on a
hypertable the recorder is actively inserting into, is a locking/WAL risk
out of proportion to what this incident needs fixed tonight. The engine's
own `_latest_quote_with_failover` (orchestrator.py) already makes any
still-open position self-heal onto the live sibling on its next exit
tick, and `upsert_instrument`'s ordering fix stops the two rows from
flip-flopping going forward — so leaving `option_quotes` split for now is
inert, not silently wrong. Loser `instruments` rows are therefore NOT
deleted here either (option_quotes still FK-references them), and the
guard-rail unique index on `(exchange, provider_ids->>'upstox')` is
deferred to the same follow-up migration that finishes the option_quotes
merge and can then actually delete the losers. Do that in a maintenance
window, not live.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d3e9f1a2b4c6"
down_revision: str | Sequence[str] | None = "b2d3f4a5c6e7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (table, fk_column) — instrument_id/underlying_id is a plain FK column,
# not part of a composite primary key, so a blind UPDATE cannot collide.
_SIMPLE_TABLES = [
    ("trades", "instrument_id"),
    ("trades", "underlying_id"),
    ("orders", "instrument_id"),
    ("signals", "underlying_id"),
    ("chain_snapshots", "underlying_id"),
    ("instruments", "underlying_id"),  # self-referential
]

# (table, fk_column, other_primary_key_columns) — instrument_id is part of
# a composite primary key here, so a loser-side row could collide with one
# the survivor already has at the same (other key columns). Small tables
# only (checked live: all under 7k affected rows); option_quotes excluded
# on purpose, see module docstring.
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

    # Union the loser's provider_ids/first_seen onto the survivor so the
    # survivor row carries whatever the eventual full merge would need —
    # cheap now, and it means the follow-up migration doesn't have to
    # redo this part.
    conn.execute(
        sa.text(
            """
            UPDATE instruments s
            SET provider_ids = s.provider_ids || l.provider_ids,
                first_seen = LEAST(s.first_seen, l.first_seen),
                underlying_symbol = COALESCE(s.underlying_symbol, l.underlying_symbol),
                segment = COALESCE(s.segment, l.segment)
            FROM instrument_merge_map m
            JOIN instruments l ON l.instrument_id = m.loser_id
            WHERE s.instrument_id = m.survivor_id
            """
        )
    )


def downgrade() -> None:
    # Not reversible: which FK rows used to point at which loser id is not
    # recorded anywhere once this has run. The loser `instruments` rows
    # themselves are untouched by this migration (still on disk, still
    # holding their own option_quotes history), so nothing is destroyed —
    # there is just nothing here to reverse.
    pass
