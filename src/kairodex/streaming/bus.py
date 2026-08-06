"""Thin Redis pub/sub wrapper — the first real consumer of
`Settings.redis_url`/`redis` (P1 §4a noted this stayed unbuilt: "no
consumer exists until P3 (engine) or P6 (API WS fanout)"). One global
channel (`kairodex.streaming.types.REDIS_CHANNEL`), not one per segment —
at this codebase's message volume (4 segments, 60s tick cadence) a
single channel with client-side filtering is simpler than N subscriptions
for no real throughput cost.
"""

from __future__ import annotations

import logging

import redis.asyncio as redis

from kairodex.config import get_settings
from kairodex.streaming.types import REDIS_CHANNEL, StreamMessage

logger = logging.getLogger(__name__)

_client: redis.Redis | None = None


def get_redis() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.from_url(get_settings().redis_url, decode_responses=True)
    return _client


async def publish(message: StreamMessage) -> None:
    """Best-effort: a Redis hiccup must never take down the live engine —
    the WS stream is a display convenience, not a correctness path.
    `trade_events`/`trades` remain the source of truth regardless of
    whether this publish ever reaches a subscriber."""
    try:
        client = get_redis()
        await client.publish(REDIS_CHANNEL, message.model_dump_json())
    except Exception as e:
        # No exc_info — this fires once per publish call while Redis is
        # down (up to ~4/tick across 4 segments, indefinitely), and a
        # full traceback per call floods the journal for a known,
        # already-explained, non-fatal condition. The message alone is
        # enough to notice and diagnose it.
        logger.warning("stream publish failed (non-fatal): %s", e)
