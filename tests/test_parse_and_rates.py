from __future__ import annotations

from pathlib import Path

from caretrace.audit.estimate import estimate_oop
from caretrace.parse.detect import detect_format
from caretrace.pipeline import SAMPLES, load_samples
from caretrace.warehouse import get_rates, negotiated_resolution_rate

V3 = """hospital_name,last_updated_on,version
Massachusetts General Hospital,2026-03-27,3.0.0
description,code|1,code|1|type,code|2,code|2|type,setting,payer_name,plan_name,standard_charge|negotiated_dollar,standard_charge|gross
"MRI Lwr Ext Joint W/O Cont",PX-61000062,CDM,73721,CPT,both,UNITED HEALTHCARE [1016],HB MGH,281.29,3332.00
"""

WIDE_V3 = """hospital_name,last_updated_on,version
Beth Israel,2026-01-01,3.0.0
description,code|1,code|1|type,setting,standard_charge|gross,standard_charge|Aetna|HMO|negotiated_dollar
Knee MRI,73721,CPT,outpatient,100,80
"""


def test_detect_samples() -> None:
    root = Path("data/samples")
    assert detect_format(root / "mgh_mrf.json") == "json_v2"
    assert detect_format(root / "bwh_mrf_tall.csv") == "csv_tall"
    assert detect_format(root / "bidmc_mrf_wide.csv") == "csv_wide"


def test_v3_preamble_keeps_cpt(tmp_path: Path) -> None:
    path = tmp_path / "mgh.csv"
    path.write_text(V3, encoding="utf-8")
    assert detect_format(path) == "csv_tall"
    from caretrace.parse.csv_tall import iter_rows

    rows = list(iter_rows(path, source_id="mgh", file_id="live", source_file_uri=str(path)))
    by_code = {row["code"]: row for row, reason in rows if reason is None}
    assert set(by_code) == {"PX-61000062", "73721"}
    cpt = by_code["73721"]
    assert cpt["code_type"] == "CPT"
    assert cpt["negotiated_dollar"] == 281.29
    assert cpt["hospital_name"] == "Massachusetts General Hospital"
    assert cpt["version"] == "3.0.0"
    assert cpt["last_updated_on"] == "2026-03-27"
    assert by_code["PX-61000062"]["code_type"] == "LOCAL"


def test_v3_wide_preamble(tmp_path: Path) -> None:
    path = tmp_path / "wide.csv"
    path.write_text(WIDE_V3, encoding="utf-8")
    assert detect_format(path) == "csv_wide"
    from caretrace.parse.csv_wide import iter_rows

    rows = [
        row
        for row, reason in iter_rows(
            path, source_id="bidmc", file_id="live", source_file_uri=str(path)
        )
        if reason is None
    ]
    assert len(rows) == 1
    assert rows[0]["code"] == "73721"
    assert rows[0]["negotiated_dollar"] == 80
    assert rows[0]["payer_name_raw"] == "Aetna"
    assert rows[0]["version"] == "3.0.0"


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
