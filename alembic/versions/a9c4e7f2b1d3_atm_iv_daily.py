"""atm_iv_daily: per-session ATM implied volatility per underlying

Revision ID: a9c4e7f2b1d3
Revises: c2a6f4b8d1e7
Create Date: 2026-09-15 17:00:00.000000

The history `iv_rank`/`iv_percentile` need. Both features were MISSING on
100% of feature vectors because nothing supplied `FeatureContext.iv_history`
(PROGRESS.md §20e). A small plain table, not a hypertable: ~22 rows/session.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a9c4e7f2b1d3"
down_revision: str | Sequence[str] | None = "c2a6f4b8d1e7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "atm_iv_daily",
        sa.Column(
            "instrument_id",
            sa.BigInteger(),
            sa.ForeignKey("instruments.instrument_id"),
            primary_key=True,
        ),
        sa.Column("session_date", sa.Date(), primary_key=True),
        sa.Column("atm_iv", sa.Numeric(10, 6), nullable=False),
        sa.Column("n_quotes", sa.Integer(), nullable=False),
    )
    # ALTER DEFAULT PRIVILEGES (f1ac2a76a6ba) should already cover this, but
    # §24 showed role state does not survive a cluster restore; grant it
    # explicitly so the engine's least-privilege role can always read it.
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'kairodex_app') THEN "
        "GRANT SELECT, INSERT, UPDATE, DELETE ON atm_iv_daily TO kairodex_app; END IF; END $$"
    )


def downgrade() -> None:
    op.drop_table("atm_iv_daily")
