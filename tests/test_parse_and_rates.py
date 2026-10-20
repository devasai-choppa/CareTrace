from __future__ import annotations

from pathlib import Path

from caretrace.audit.estimate import estimate_oop
from caretrace.parse.detect import detect_format
from caretrace.pipeline import SAMPLES, load_samples
from caretrace.warehouse import get_rates, negotiated_resolution_rate


def test_detect_samples() -> None:
    root = Path("data/samples")
    assert detect_format(root / "mgh_mrf.json") == "json_v2"
    assert detect_format(root / "bwh_mrf_tall.csv") == "csv_tall"
    assert detect_format(root / "bidmc_mrf_wide.csv") == "csv_wide"


def test_samples_build_cited_quotes(settings) -> None:
    load_samples(settings)
    assert negotiated_resolution_rate(settings) >= 0.85
    quotes = get_rates("73721", "bcbsma", zip_code="02115", settings=settings)
    assert len(quotes) >= 5
    hospitals = {item.value.source_id for item in quotes}
    assert hospitals == {row[0] for row in SAMPLES}
    for item in quotes:
        assert item.lineage
        assert item.lineage[0].source_row_hash
        assert item.confidence > 0
        assert item.value.rate_dollar is not None


def test_estimate_table() -> None:
    result = estimate_oop(1000, 200, 0.2, 50, 5000)
    assert result.value.patient_responsibility == 400
    assert result.value.copay_applied == 50
    assert result.value.deductible_applied == 200
    assert result.value.coinsurance_applied == 150
    capped = estimate_oop(1000, 200, 0.2, 50, 100)
    assert capped.value.patient_responsibility == 100
    assert capped.value.assumptions
