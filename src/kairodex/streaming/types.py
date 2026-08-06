"""The discriminated union ARCHITECTURE.md §15 names: "typed messages...
tick, signal, position_update, trade_closed, risk_update, feed_health."
"""

from __future__ import annotations

import datetime
from typing import Any, Literal

from pydantic import BaseModel

MessageType = Literal[
    "tick", "signal", "position_update", "trade_closed", "risk_update", "feed_health"
]

REDIS_CHANNEL = "kairodex:stream"


class StreamMessage(BaseModel):
    type: MessageType
    # None only for feed_health (provider-scoped, not segment-scoped) —
    # every other message type always carries a real segment.
    segment: str | None
    ts: datetime.datetime
    data: dict[str, Any]
