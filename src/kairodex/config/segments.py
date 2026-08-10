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

    # Minutes into the session before this segment may open anything.
    # Live 2026-08-05..10, 11 of 13 NSE entries were taken in the first
    # 20 minutes of the session and 5 in the first 4 — and those opening
    # entries are where the losses are. It is not bad luck: ATR, VWAP,
    # opening-range position, volume-profile POC and price acceptance all
    # need intraday history that does not exist yet at 09:18, so the
    # confluence score at the bell is built largely from degenerate
    # inputs. Waiting also stops the whole watchlist being judged in one
    # burst against empty slots (§14c's first-come-first-served shape).
    entry_warmup_minutes: int

    # Minutes before the close after which no new entries are opened.
    # Everything is intraday (nothing held overnight), so a position
    # opened near the bell is force-closed before it can work — bought and
    # sold for the two spreads. Same 90 minutes as the scratch window, and
    # from the same measurement.
    entry_cutoff_minutes: int

    # Theta guard, in whole trading sessions rather than calendar days —
    # `core.sessions.session_seconds_between` does the conversion.
    max_holding_sessions: float

    # Scratch rule (monitor.scratch_exit_check): a position that has not
    # shown `scratch_exit_min_mfe_pct` favourable excursion within
    # `scratch_exit_after_minutes` of session time is closed rather than
    # left to run to its full stop. Both derived from the first ten closed
    # trades' own excursion timing — see that function's docstring for the
    # numbers. Per-segment because the observed time-to-work differs by
    # market, and tunable because these are the two knobs most worth
    # recalibrating once there are more closed trades to fit against.
    scratch_exit_after_minutes: int
    scratch_exit_min_mfe_pct: float


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
