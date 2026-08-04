"""`kairodex jobs` (ARCHITECTURE.md §3): APScheduler-driven periodic checks.

P1 scope is just the annual Upstox token-expiry check named in the roadmap
row ("annual token-expiry alerting"). EOD rollups, exports, retention, and
FX snapshot are also listed against this process in §3, but none has a
consumer yet (rollups feed §5.5 tables P5 creates, exports are P5's bundle,
retention is already handled by Timescale's own policies from the P0
migration) — adding empty-handed jobs now would be scaffolding with nothing
to run. Add each when its actual consumer lands.

Delivery is a log line, not a push notification (docs/PROGRESS.md decision,
2026-08-04): `kairodex status` is the pull-based surface for this in P1;
desktop/webhook delivery is deferred until something actually needs to be
paged rather than checked.
"""

from __future__ import annotations

import asyncio
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from kairodex.config import get_settings
from kairodex.data.upstox.auth import AnalyticsToken

logger = logging.getLogger(__name__)


def check_upstox_token_expiry() -> None:
    settings = get_settings()
    if settings.upstox_access_token is None:
        return  # NSE not configured on this deployment — nothing to check
    token = AnalyticsToken(settings.upstox_access_token, settings.upstox_token_expires_at)
    days = token.days_until_expiry()
    if token.is_expiring_soon():
        logger.warning("upstox token expiring soon: %s days remaining", days)
    else:
        logger.info("upstox token expiry OK: %s days remaining", days)


async def _run_forever() -> None:
    scheduler = AsyncIOScheduler()
    # Once/day is enough for a check against a ~1 year token (ADR 0006) —
    # this is the annual-reminder job, not a daily-reauth one.
    scheduler.add_job(check_upstox_token_expiry, CronTrigger(hour=6, minute=0))
    scheduler.start()
    check_upstox_token_expiry()  # also run once at startup, don't wait a day to notice
    await asyncio.Event().wait()


def run() -> None:
    asyncio.run(_run_forever())
