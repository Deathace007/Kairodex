"""hard-delete US segment (us_stock/us_index) data, part 1: trading tables
and market data other than option_quotes

Revision ID: e4b8a2c6d9f1
Revises: b6f5e4d3c2a1
Create Date: 2026-08-20 12:30:00.000000

US segment removal (docs/PROGRESS.md) -- services already stopped and
disabled on the VM, config/watchlist.yaml already dropped the us_index/
us_stock blocks. Code paths are left in place (stop+disable, not a code
removal -- Market.US/LSE code touches ~65 files, a separate effort).

Split into three revisions, same reasoning as the NSE instrument merge
(f7a1b3c5d8e0 / a1b2c3d4e5f6 / b6f5e4d3c2a1): isolate the one genuinely
slow step (option_quotes, ~21.8M US rows on a compressed hypertable) from
everything else, so a problem in a fast step never costs redoing the slow
one.

This revision: every FK child in delete order, down to (but not
including) option_quotes -- trade_events -> position_marks -> fills ->
orders -> trades -> equity_snapshots -> risk_state -> backtest_runs ->
signals -> feature_vectors -> chain_snapshots -> options_flow ->
market_depth -> underlying_bars -> instrument_specs ->
watchlist_membership -> corporate_actions. `strategies` and
`strategy_promotions` are deliberately left alone (dormant config, not
data to delete) -- nothing here touches them.

None of these tables are large (max ~267k rows, underlying_bars) so this
runs in seconds, not hours -- no decompression handling needed here.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e4b8a2c6d9f1"
down_revision: str | Sequence[str] | None = "b6f5e4d3c2a1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_US_SEGMENTS = "('us_stock', 'us_index')"


def upgrade() -> None:
    conn = op.get_bind()

    conn.execute(
        sa.text(
            """
            DELETE FROM trade_events
            WHERE trade_id IN (SELECT trade_id FROM trades WHERE segment IN """
            + _US_SEGMENTS
            + """)
            """
        )
    )
    conn.execute(
        sa.text(
            """
            DELETE FROM position_marks
            WHERE trade_id IN (SELECT trade_id FROM trades WHERE segment IN """
            + _US_SEGMENTS
            + """)
            """
        )
    )
    conn.execute(
        sa.text(
            """
            DELETE FROM fills
            WHERE order_id IN (
                SELECT order_id FROM orders
                WHERE trade_id IN (SELECT trade_id FROM trades WHERE segment IN """
            + _US_SEGMENTS
            + """)
            )
            """
        )
    )
    conn.execute(
        sa.text(
            """
            DELETE FROM orders
            WHERE trade_id IN (SELECT trade_id FROM trades WHERE segment IN """
            + _US_SEGMENTS
            + """)
            """
        )
    )
    conn.execute(sa.text("DELETE FROM trades WHERE segment IN " + _US_SEGMENTS))
    conn.execute(sa.text("DELETE FROM equity_snapshots WHERE segment IN " + _US_SEGMENTS))
    conn.execute(sa.text("DELETE FROM risk_state WHERE segment IN " + _US_SEGMENTS))
    conn.execute(sa.text("DELETE FROM backtest_runs WHERE segment IN " + _US_SEGMENTS))
    conn.execute(sa.text("DELETE FROM signals WHERE segment IN " + _US_SEGMENTS))

    conn.execute(
        sa.text(
            """
            DELETE FROM feature_vectors
            WHERE instrument_id IN (SELECT instrument_id FROM instruments WHERE exchange = 'US')
            """
        )
    )
    conn.execute(
        sa.text(
            """
            DELETE FROM chain_snapshots
            WHERE underlying_id IN (SELECT instrument_id FROM instruments WHERE exchange = 'US')
            """
        )
    )
    conn.execute(
        sa.text(
            """
            DELETE FROM options_flow
            WHERE instrument_id IN (SELECT instrument_id FROM instruments WHERE exchange = 'US')
            """
        )
    )
    conn.execute(
        sa.text(
            """
            DELETE FROM market_depth
            WHERE instrument_id IN (SELECT instrument_id FROM instruments WHERE exchange = 'US')
            """
        )
    )
    conn.execute(
        sa.text(
            """
            DELETE FROM underlying_bars
            WHERE instrument_id IN (SELECT instrument_id FROM instruments WHERE exchange = 'US')
            """
        )
    )
    conn.execute(
        sa.text(
            """
            DELETE FROM instrument_specs
            WHERE instrument_id IN (SELECT instrument_id FROM instruments WHERE exchange = 'US')
            """
        )
    )
    conn.execute(
        sa.text(
            """
            DELETE FROM watchlist_membership
            WHERE instrument_id IN (SELECT instrument_id FROM instruments WHERE exchange = 'US')
            """
        )
    )
    conn.execute(
        sa.text(
            """
            DELETE FROM corporate_actions
            WHERE instrument_id IN (SELECT instrument_id FROM instruments WHERE exchange = 'US')
            """
        )
    )


def downgrade() -> None:
    # Not reversible -- restore from the pre-delete backup
    # (/root/backups/us_segment_removal_2026-08-20/ on the VM) if this
    # ever needs undoing.
    pass
