"""app_role_separation

Revision ID: f1ac2a76a6ba
Revises: 5256d032cc30
Create Date: 2026-08-05 15:06:50.104220

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'f1ac2a76a6ba'
down_revision: Union[str, Sequence[str], None] = '5256d032cc30'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Creates the least-privilege role the app runtime connects as
    (ARCHITECTURE.md Principle 2, §5.4) — separate from the `kairodex`
    role migrations use, which is a superuser on this deployment (checked
    live: `\\du` shows `kairodex | Superuser, ...`). That matters because
    the next migration's `REVOKE UPDATE, DELETE ON trade_events` is a
    complete no-op against a superuser connection — Postgres superusers
    bypass every grant/revoke check. Without this role existing first,
    implementing that REVOKE would be security theater: it would read as
    enforcing the append-only guarantee while actually enforcing nothing.

    `ALTER DEFAULT PRIVILEGES` covers tables created by *future* migrations
    too, so nothing needs to remember to re-grant kairodex_app access every
    time a new table shows up — the explicit REVOKE on trade_events in the
    next migration is the only place that needs to override the default.
    """
    from kairodex.config import get_settings

    password = get_settings().app_db_password
    if not password:
        raise RuntimeError(
            "APP_DB_PASSWORD must be set in .env before running this "
            "migration — see .env.example."
        )

    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'kairodex_app') THEN
                CREATE ROLE kairodex_app LOGIN PASSWORD '{password}';
            ELSE
                ALTER ROLE kairodex_app LOGIN PASSWORD '{password}';
            END IF;
        END
        $$;
        """
    )
    op.execute("GRANT USAGE ON SCHEMA public TO kairodex_app")
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO kairodex_app"
    )
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO kairodex_app")
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO kairodex_app"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        "GRANT USAGE, SELECT ON SEQUENCES TO kairodex_app"
    )


def downgrade() -> None:
    op.execute("ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM kairodex_app")
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM kairodex_app"
    )
    op.execute("REVOKE ALL ON ALL TABLES IN SCHEMA public FROM kairodex_app")
    op.execute("REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM kairodex_app")
    op.execute("REVOKE USAGE ON SCHEMA public FROM kairodex_app")
    op.execute("DROP ROLE IF EXISTS kairodex_app")
