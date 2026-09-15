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
import datetime
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from kairodex.config import get_settings
from kairodex.core.enums import Market, Segment
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


# Days of signals each nightly run re-scans. The resolver skips rows that
# already have an outcome, so this only bounds how far back a missed night
# gets caught up — a week covers a long weekend plus a dead jobs process.
OUTCOME_RESCAN_DAYS = 7


async def resolve_recent_outcomes() -> None:
    """Label the day's signals with their forward outcome.

    `kairodex backtest backfill-outcomes` had only ever been run by hand,
    once: the newest labelled signal on 2026-09-15 was from 2026-08-13 and
    8,243 older signals had no outcome — a month in which nothing the
    system decided could be measured against what the market then did."""
    from kairodex.backtest.backfill import backfill_forward_outcomes
    from kairodex.store.base import get_sessionmaker

    since = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=OUTCOME_RESCAN_DAYS)
    sessionmaker = get_sessionmaker()
    for segment in (s for s in Segment if s.market is Market.NSE):
        try:
            async with sessionmaker() as session:
                stats = await backfill_forward_outcomes(session, segment=segment, since=since)
            logger.info(
                "%s outcomes: scanned=%d written=%d unresolved=%d",
                segment.value, stats.scanned, stats.written, stats.unresolved,
            )
        except Exception:
            logger.exception("%s outcome resolution failed", segment.value)


async def _run_forever() -> None:
    scheduler = AsyncIOScheduler()
    # Once/day is enough for a check against a ~1 year token (ADR 0006) —
    # this is the annual-reminder job, not a daily-reauth one.
    scheduler.add_job(check_upstox_token_expiry, CronTrigger(hour=6, minute=0))
    # After the 15:30 IST close plus the bar refresh; explicit timezone
    # because the VM clock is UTC.
    scheduler.add_job(
        resolve_recent_outcomes, CronTrigger(hour=16, minute=15, timezone="Asia/Kolkata")
    )
    scheduler.start()
    check_upstox_token_expiry()  # also run once at startup, don't wait a day to notice
    await asyncio.Event().wait()


def run() -> None:
    asyncio.run(_run_forever())
