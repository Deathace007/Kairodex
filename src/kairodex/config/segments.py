"""Per-segment risk config (ARCHITECTURE.md §11) — `config/segments/*.yaml`,
one file per `Segment`. Loaded once and cached, same pattern as
`kairodex.config.settings.get_settings`.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict

from kairodex.core.enums import Segment

# CWD-relative, same convention as the CLI's watchlist-file default
# (kairodex processes always run from the repo root — see cli.py).
_SEGMENTS_DIR = Path("config/segments")


class SegmentRiskConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    capital: float
    currency: str
    base_risk_pct: float
    hard_ceiling_pct: float
    max_premium_pct: float
    max_concurrent: int
    daily_loss_limit_pct: float
    weekly_loss_limit_pct: float
    max_drawdown_pct: float
    exposure_cap_pct: float
    min_liquidity_score: float
    reentry_cooldown_minutes: int
    # Confluence score below which a signal is never worth a slot. Without
    # it the engine was purely first-come-first-served: it filled every
    # concurrent slot in the opening tick with whatever the watchlist
    # happened to iterate over first, then spent the rest of the session
    # rejecting everything on MAX_CONCURRENT. Live 2026-08-07, nse_stock
    # opened 4 of its 5 slots in a single tick — one at confidence 0.1366 —
    # and then rejected 12,696 later signals, some as high as 0.7344.
    min_confidence: float


@lru_cache
def get_segment_config(segment: Segment) -> SegmentRiskConfig:
    path = _SEGMENTS_DIR / f"{segment.value}.yaml"
    with path.open() as f:
        raw = yaml.safe_load(f)
    config = SegmentRiskConfig.model_validate(raw)
    if config.currency != segment.currency:
        raise ValueError(
            f"{path}: currency={config.currency!r} doesn't match "
            f"Segment.{segment.name}.currency={segment.currency!r}"
        )
    return config
