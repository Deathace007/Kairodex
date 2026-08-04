import datetime
import logging

from kairodex import jobs


class _FakeSettings:
    def __init__(self, expires_at: datetime.date | None) -> None:
        self.upstox_access_token = "fake-token"
        self.upstox_token_expires_at = expires_at


def test_expiring_token_logs_warning(monkeypatch, caplog):
    soon = datetime.date.today() + datetime.timedelta(days=5)
    monkeypatch.setattr(jobs, "get_settings", lambda: _FakeSettings(soon))
    with caplog.at_level(logging.WARNING, logger="kairodex.jobs"):
        jobs.check_upstox_token_expiry()
    assert any("expiring soon" in r.message for r in caplog.records)


def test_healthy_token_logs_info(monkeypatch, caplog):
    far = datetime.date.today() + datetime.timedelta(days=200)
    monkeypatch.setattr(jobs, "get_settings", lambda: _FakeSettings(far))
    with caplog.at_level(logging.INFO, logger="kairodex.jobs"):
        jobs.check_upstox_token_expiry()
    assert any("expiry OK" in r.message for r in caplog.records)
    assert not any(r.levelno >= logging.WARNING for r in caplog.records)


def test_missing_token_is_a_noop(monkeypatch, caplog):
    settings = _FakeSettings.__new__(_FakeSettings)
    settings.upstox_access_token = None
    monkeypatch.setattr(jobs, "get_settings", lambda: settings)
    with caplog.at_level(logging.INFO, logger="kairodex.jobs"):
        jobs.check_upstox_token_expiry()
    assert caplog.records == []
