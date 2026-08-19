"""finish NSE instrument merge — option_quotes, loser deletion, guard rail

Revision ID: f7a1b3c5d8e0
Revises: d3e9f1a2b4c6
Create Date: 2026-08-19 15:45:00.000000

The maintenance-window half of `d3e9f1a2b4c6`, deferred there because
46.8M of `option_quotes`'s ~102.6M rows belonged to loser instrument_ids
and rewriting that live during market hours was the wrong tradeoff. NSE
market is now closed for the day (last quote 2026-08-19 15:40 IST, engine
logged "market closed" for both nse_stock and nse_index at 15:31-15:32),
the recorder has gone quiet, and there are 0 open positions — so this
finishes the job:

  - recomputes the identical loser/survivor mapping `d3e9f1a2b4c6` used
    (same ordering, and that migration didn't touch `last_seen`, so the
    ranking is unchanged) and merges `option_quotes` the same
    collision-safe way the small keyed tables were merged: drop any
    loser-side row that would collide with one the survivor already has
    at the same `ts`, then repoint the rest.
  - deletes the now-fully-childless loser `instruments` rows.
  - adds the guard-rail partial unique indexes on
    `(exchange, provider_ids->>'upstox')` and
    `(exchange, provider_ids->>'lse')` so this duplicate-identity class
    becomes structurally impossible, not just code-guarded
    (`ingest.upsert_instrument`'s deterministic ordering).

A scoped backup of the affected `option_quotes` rows (loser instrument_ids
only, ~46.8M rows) was taken via `COPY ... TO PROGRAM 'gzip > ...'` before
this ran — see the VM's `/tmp/loser_option_quotes_backup.csv.gz` /
`backups/`.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f7a1b3c5d8e0"
down_revision: str | Sequence[str] | None = "d3e9f1a2b4c6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()

    # option_quotes is a compressed hypertable — its older chunks are
    # TimescaleDB-compressed, and writing to a compressed chunk means
    # decompressing the rows it touches first. The default per-transaction
    # cap (100,000 tuples) exists to catch runaway DML, but this merge's
    # ~46.8M affected rows are a known, one-time, already-scoped volume,
    # not a runaway query — SET LOCAL keeps the raised limit inside this
    # migration's own transaction only.
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

    # Raising the tuple-decompression cap above only lifts DELETE's limit.
    # UPDATE on a compressed chunk is refused outright by TimescaleDB
    # ("cannot update column ... of a compressed chunk — decompress the
    # chunk before running this update") regardless of that setting, so
    # the chunks actually have to be decompressed first. `if_not_compressed
    # => true` no-ops the 7 of 12 option_quotes chunks that are already
    # uncompressed rather than erroring on them. Not recompressed
    # afterward here — the existing compression policy job picks that up
    # on its normal schedule; this migration only needs the write to
    # succeed, not to manage storage layout.
    conn.execute(
        sa.text(
            """
            SELECT decompress_chunk(c, if_not_compressed => true)
            FROM show_chunks('option_quotes') c
            """
        )
    )

    # option_quotes' primary key is (instrument_id, ts) — same collision
    # risk as the small keyed tables in d3e9f1a2b4c6, same fix: drop the
    # loser-side row first wherever the survivor already has one at the
    # same ts, then repoint what's left.
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

    # Every FK table (including option_quotes, just above) now points
    # only at survivors — the loser rows are childless and safe to drop.
    conn.execute(
        sa.text(
            """
            DELETE FROM instruments
            WHERE instrument_id IN (SELECT loser_id FROM instrument_merge_map)
            """
        )
    )

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
    # The merge and the loser-row deletion are not reversible — restore
    # from the pre-migration backup (instruments + option_quotes) if this
    # ever needs to be undone.
