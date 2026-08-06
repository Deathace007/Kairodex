import pytest
from pydantic import ValidationError

from kairodex.core.enums import Segment
from kairodex.export.research_import import FindingsImport


def test_findings_import_requires_findings_field():
    with pytest.raises(ValidationError):
        FindingsImport.model_validate({"segment": "nse_stock"})


def test_findings_import_minimal_valid():
    parsed = FindingsImport.model_validate({"findings": [{"summary": "no edge yet"}]})
    assert parsed.segment is None
    assert parsed.source == "claude-code"
    assert parsed.status == "open"
    assert parsed.applied_strategy_ids == []


def test_findings_import_full():
    parsed = FindingsImport.model_validate(
        {
            "segment": "us_stock",
            "bundle_id": "bundle_us_stock_2026-07",
            "findings": [{"summary": "vol_regime breakdown shows edge only when expanding"}],
            "actions": [{"strategy_id": 3, "note": "gate entries on vol_regime > 1.0"}],
            "applied_strategy_ids": [3],
            "status": "applied",
        }
    )
    assert parsed.segment is Segment.US_STOCK
    assert parsed.applied_strategy_ids == [3]
    assert parsed.status == "applied"


def test_findings_import_rejects_unknown_segment():
    with pytest.raises(ValidationError):
        FindingsImport.model_validate({"segment": "not_a_segment", "findings": []})
