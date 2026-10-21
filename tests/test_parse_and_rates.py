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


def test_inch_mark_quote_stays_in_description(tmp_path: Path) -> None:
    path = tmp_path / "bmc.csv"
    path.write_text(
        "hospital_name,last_updated_on,version\n"
        "Boston Medical Center,03/13/2026,3.0.0\n"
        "description,code|1,code|1|type,setting,payer_name,plan_name,standard_charge|negotiated_dollar\n"
        '"DILATOR 9"X 1",86023,CPT,both,BCBS,HMO,12.50\n',
        encoding="utf-8",
    )
    from caretrace.parse.csv_tall import iter_rows

    rows = [
        row
        for row, reason in iter_rows(
            path, source_id="bmc", file_id="live", source_file_uri=str(path)
        )
        if reason is None
    ]
    assert len(rows) == 1
    assert rows[0]["code"] == "86023"
    assert rows[0]["description"] == 'DILATOR 9"X 1'
    assert rows[0]["negotiated_dollar"] == 12.5


def test_inch_mark_before_paren_stays_in_description(tmp_path: Path) -> None:
    path = tmp_path / "bwh.csv"
    path.write_text(
        "hospital_name,last_updated_on,version\n"
        "Brigham and Women's Hospital,2026-03-27,3.0.0\n"
        "description,code|1,code|1|type,setting,payer_name,plan_name,standard_charge|negotiated_dollar\n"
        '"Wound Dress 4"X 5" (129 Sq Cm)",Q4104,HCPCS,outpatient,BCBS,HMO,12.50\n',
        encoding="utf-8",
    )
    from caretrace.parse.csv_tall import iter_rows

    rows = [
        row
        for row, reason in iter_rows(
            path, source_id="bwh", file_id="live", source_file_uri=str(path)
        )
        if reason is None
    ]
    assert len(rows) == 1
    assert rows[0]["code"] == "Q4104"
    assert rows[0]["description"] == 'Wound Dress 4"X 5" (129 Sq Cm)'
    assert rows[0]["negotiated_dollar"] == 12.5


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


def test_live_bronze_feeds_marts(settings) -> None:
    """A downloaded-shaped live CSV rebuilds gold with a published BCBS dollar."""
    from caretrace.ingest.download import file_id_for
    from caretrace.ingest.manifest import record_download
    from caretrace.parse.payer_resolve import resolve_all
    from caretrace.parse.runner import parse_file
    from caretrace.pipeline import _clear_charge_bronze
    from caretrace.warehouse import rebuild_marts

    uri = "https://example.invalid/bwh_standardcharges.csv"
    file_id = file_id_for("bwh", uri)
    raw_dir = settings.raw_dir / "bwh"
    raw_dir.mkdir(parents=True)
    path = raw_dir / f"{file_id}_charges.csv"
    path.write_text(
        "hospital_name,last_updated_on,version\n"
        "Brigham and Women's Hospital,2026-03-27,3.0.0\n"
        "description,code|1,code|1|type,code|2,code|2|type,setting,"
        "payer_name,plan_name,standard_charge|negotiated_dollar,"
        "standard_charge|gross,standard_charge|discounted_cash\n"
        "Knee MRI,PX-1,CDM,73721,CPT,outpatient,"
        "BLUE CROSS BLUE SHIELD [110001],HB AMC BLUE CROSS HMO,966.58,3332.00,2499.00\n"
        "Other,99999,CPT,outpatient,Aetna,HMO,12.00,20.00,15.00\n",
        encoding="utf-8",
    )
    record_download(file_id, "bwh", uri, "abc", path.stat().st_size, settings=settings)
    _clear_charge_bronze(settings)
    stats = parse_file(
        path,
        source_id="bwh",
        file_id=file_id,
        source_file_uri=uri,
        hospital_name="Brigham and Women's Hospital",
        settings=settings,
        code_allowlist={"73721"},
    )
    assert stats["rows"] >= 1
    assert stats["skipped_codes"] >= 1
    resolve_all(settings)
    rebuild_marts(settings, codes={"73721"})
    quotes = get_rates("73721", "bcbsma", zip_code="02115", settings=settings)
    assert len(quotes) == 1
    assert quotes[0].value.rate_dollar == 966.58
    assert quotes[0].value.source_id == "bwh"
    assert quotes[0].lineage[0].source_file_uri == uri
    assert quotes[0].lineage[0].source_row_hash
