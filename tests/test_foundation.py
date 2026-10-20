from __future__ import annotations

from pathlib import Path

import pytest
from caretrace.ingest.manifest import list_pending, mark_parsed, record_download
from caretrace.ingest.registry import load_registry
from caretrace.metrics import quality_score, transparency_score
from caretrace.models.lineage import Cited, row_hash
from caretrace.phi import PhiRejectedError, assert_no_phi
from pydantic import ValidationError


def test_registry_loads() -> None:
    registry = load_registry(Path("config/sources.yaml"))
    assert len(registry.hospitals) == 5
    assert len(registry.payers) == 3
    assert registry.payer("bcbsma") is not None


def test_registry_rejects_bad_yaml(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("hospitals: []\npayers: [{canonical_name: nope}]\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        load_registry(path)


def test_row_hash_ignores_key_order() -> None:
    assert row_hash({"b": 1, "a": 2}) == row_hash({"a": 2, "b": 1})
    assert row_hash({"a": 1}) != row_hash({"a": 2})


def test_cited_bounds() -> None:
    with pytest.raises(ValidationError):
        Cited(value=1, confidence=1.5)


def test_manifest_roundtrip(settings) -> None:
    record_download("f1", "mgh", "https://example.invalid/a.json", "abc", 10, settings=settings)
    pending = list_pending(settings)
    assert pending[0]["file_id"] == "f1"
    mark_parsed("f1", settings=settings)
    assert list_pending(settings) == []


def test_phi_guard() -> None:
    with pytest.raises(PhiRejectedError):
        assert_no_phi("ssn 123-45-6789", demo_mode=True)
    assert_no_phi("123-45-6789", demo_mode=False)


def test_score_formulas() -> None:
    assert (
        quality_score(has_dollar=True, percent_only=False, algorithm_only=False, outlier=False) == 1
    )
    assert transparency_score(10, 1, 1, 0) == pytest.approx(1)
    assert transparency_score(730, 0, 0, 1) == 0
