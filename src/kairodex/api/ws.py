"""ARCHITECTURE.md §15/§16 — `WS /ws/stream?segments=...`, "one socket
for the whole UI." Subscribes to the single Redis pub/sub channel
`kairodex.streaming` publishes to, filters by the requested segments
client-side (cheap at this codebase's message volume — see
`kairodex.streaming.bus`'s own docstring), and forwards every matching
message verbatim as JSON text frames.
"""

from __future__ import annotations

import contextlib
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from kairodex.streaming.bus import get_redis
from kairodex.streaming.types import REDIS_CHANNEL

logger = logging.getLogger(__name__)

router = APIRouter()


@router.websocket("/ws/stream")
async def stream(websocket: WebSocket) -> None:
    await websocket.accept()
    raw_segments = websocket.query_params.get("segments")
    wanted = set(raw_segments.split(",")) if raw_segments else None  # None = every segment

    pubsub = get_redis().pubsub()
    await pubsub.subscribe(REDIS_CHANNEL)
    try:
        async for message in pubsub.listen():
            if message["type"] != "message":
                continue
            payload = message["data"]
            # ponytail: string match instead of json.loads — safe because
            # the only publisher is kairodex.streaming.bus (pydantic's
            # compact model_dump_json(), no whitespace, "segment" never
            # nested elsewhere in StreamMessage), not arbitrary untrusted
            # input. Switch to a real parse if this channel ever gets a
            # second kind of publisher.
            if wanted is not None and not any(
                f'"segment":"{seg}"' in payload for seg in wanted
            ):
                continue
            try:
                await websocket.send_text(payload)
            except WebSocketDisconnect:
                break
    except WebSocketDisconnect:
        pass
    finally:
        with contextlib.suppress(Exception):
            await pubsub.unsubscribe(REDIS_CHANNEL)
            await pubsub.aclose()  # type: ignore[no-untyped-call]  # redis-py's own stub gap
