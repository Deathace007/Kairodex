import datetime

import pytest
from fastapi import HTTPException

from kairodex.api.deps import parse_window


def test_parse_window_default_is_30d():
    frm, to = parse_window(None)
    assert (to - frm).days == 30


def test_parse_window_explicit_days():
    frm, to = parse_window("7d")
    assert (to - frm).days == 7


def test_parse_window_all_starts_at_epoch_anchor():
    frm, _to = parse_window("all")
    assert frm.year == 2020


def test_parse_window_rejects_garbage():
    with pytest.raises(HTTPException) as exc_info:
        parse_window("not-a-window")
    assert exc_info.value.status_code == 422


def test_parse_window_to_is_now():
    before = datetime.datetime.now(datetime.UTC)
    _, to = parse_window("7d")
    after = datetime.datetime.now(datetime.UTC)
    assert before <= to <= after
