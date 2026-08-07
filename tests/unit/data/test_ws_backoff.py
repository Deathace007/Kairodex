"""A vendor refusing the handshake is not a dropped connection."""

from kairodex.data.recorder import (
    _WS_AUTH_REJECT_CAP,
    _WS_RECONNECT_CAP,
    _is_auth_rejection,
)


def test_auth_rejections_are_recognised():
    """websockets reports the handshake status in the exception message.
    Live 2026-08-07 Upstox returned 403 on 327 consecutive handshakes while
    the same token still served REST market data with HTTP 200."""
    assert _is_auth_rejection(Exception("server rejected WebSocket connection: HTTP 403"))
    assert _is_auth_rejection(Exception("server rejected WebSocket connection: HTTP 401"))


def test_transient_failures_are_not_treated_as_auth_rejections():
    """503s and clean drops must keep the short cap — those really do
    recover on their own, and they did on the same morning."""
    assert not _is_auth_rejection(Exception("server rejected WebSocket connection: HTTP 503"))
    assert not _is_auth_rejection(Exception("no close frame received or sent"))


def test_auth_rejections_back_off_much_harder():
    assert _WS_AUTH_REJECT_CAP > _WS_RECONNECT_CAP
