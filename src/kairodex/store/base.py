from __future__ import annotations

from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from kairodex.config import get_settings


class Base(DeclarativeBase):
    pass


@lru_cache
def get_engine() -> AsyncEngine:
    # App runtime uses the least-privilege role if configured (see Settings.
    # app_database_url) — migrations (alembic/env.py) stay on database_url,
    # the admin role, always.
    settings = get_settings()
    url = settings.app_database_url or settings.database_url
    return create_async_engine(url, pool_pre_ping=True)


@lru_cache
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), expire_on_commit=False)
