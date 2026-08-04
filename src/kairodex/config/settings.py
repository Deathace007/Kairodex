"""App-wide settings from environment / .env.

Per-segment risk parameters (capital, base_risk_pct, hard_ceiling_pct — see
ARCHITECTURE.md §11) belong in config/segments/*.yaml, added in P3 when the
risk engine exists to consume them. Nothing in P0/P1 needs them yet.
"""

from __future__ import annotations

import datetime
from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from kairodex.core.errors import ConfigError


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    kairodex_env: str = "development"
    kairodex_paper_only: bool = True

    database_url: str
    redis_url: str = "redis://localhost:6379/0"

    # Analytics Token: long-lived (1yr), read-only, no daily OAuth dance.
    # Generated manually via the Upstox Developer Apps -> Analytics tab.
    upstox_access_token: str | None = None
    upstox_token_expires_at: datetime.date | None = None

    lse_api_key: str | None = None

    @field_validator("kairodex_paper_only")
    @classmethod
    def _must_stay_paper_only(cls, v: bool) -> bool:
        # ARCHITECTURE.md §11: live trading is not a flag, it's absent code.
        # This assertion exists so a stray config edit can't quietly change that.
        if not v:
            raise ConfigError(
                "KAIRODEX_PAPER_ONLY=false is not supported. This system has no live "
                "broker integration; see docs/ARCHITECTURE.md §11."
            )
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()  # fields load from env/.env
