"""ARCHITECTURE.md §15/§16 — `WS /ws/stream?segments=...`, "one socket
for the whole UI." Subscribes to the single Redis pub/sub channel
`kairodex.streaming` publishes to, filters by the requested segments
client-side (cheap at this codebase's message volume — see
`kairodex.streaming.bus`'s own docstring), and forwards every matching
message verbatim as JSON text frames.
"""

from __future__ import annotations

import asyncio
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

    async def watch_for_disconnect() -> None:
        # The client never sends anything on this socket by design
        # (kairodex/frontend's useStream only listens) — this loop exists
        # purely to notice a close *promptly* via ASGI's own disconnect
        # signal. Without it (P6 subagent review caught this live: a
        # subscriber orphaned on the VM for 25+ minutes after its tunnel
        # dropped), the only way this handler ever learned the client was
        # gone was `send_text` failing on a *matching* message — which,
        # for a client with a narrow `?segments=` filter, could be
        # minutes away or never, leaking one Redis pub/sub connection and
        # one live task per dropped client indefinitely.
        while True:
            await websocket.receive_text()

    async def forward_messages() -> None:
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
            #
            # `"segment":null` (feed_health — provider-scoped, not
            # segment-scoped, per StreamMessage's own docstring) always
            # forwards regardless of a client's `?segments=` filter — a
            # P6 subagent review caught that without this, a filtered
            # client could never receive it at all, since no segment
            # value can ever match a literal `null`.
            is_global = '"segment":null' in payload
            if not is_global and wanted is not None and not any(
                f'"segment":"{seg}"' in payload for seg in wanted
            ):
                continue
            await websocket.send_text(payload)

    receiver = asyncio.ensure_future(watch_for_disconnect())
    sender = asyncio.ensure_future(forward_messages())
    try:
        # Either task finishing (disconnect on one side, a send/listen
        # failure on the other) is the signal to tear the whole thing
        # down — there is nothing useful left for the surviving task to
        # do once the pair has lost sync.
        await asyncio.wait({receiver, sender}, return_when=asyncio.FIRST_COMPLETED)
    except WebSocketDisconnect:
        pass
    finally:
        receiver.cancel()
        sender.cancel()
        with contextlib.suppress(Exception):
            await receiver
        with contextlib.suppress(Exception):
            await sender
        with contextlib.suppress(Exception):
            await pubsub.unsubscribe(REDIS_CHANNEL)
            await pubsub.aclose()  # type: ignore[no-untyped-call]  # redis-py's own stub gap
        with contextlib.suppress(Exception):
            await websocket.close()
