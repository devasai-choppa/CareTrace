"""Stream Synthea-style FHIR R4 bundles into an OMOP CDM subset."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from caretrace.config import Settings, get_settings

TABLES = (
    "person",
    "visit_occurrence",
    "condition_occurrence",
    "procedure_occurrence",
    "drug_exposure",
    "payer_plan_period",
)


def convert_bundles(paths: list[Path], settings: Settings | None = None) -> dict[str, int]:
    """Read bundles one at a time and write OMOP parquet tables."""
    cfg = settings or get_settings()
    rows: dict[str, list[dict[str, Any]]] = {name: [] for name in TABLES}
    for path in paths:
        bundle = json.loads(path.read_text(encoding="utf-8"))
        _consume(bundle, rows)
    counts = {}
    for name, records in rows.items():
        out = cfg.bronze_dir / "omop" / name
        out.mkdir(parents=True, exist_ok=True)
        if records:
            pq.write_table(pa.Table.from_pylist(records), out / f"{name}.parquet")
        else:
            pq.write_table(
                pa.table({key: [] for key in _empty_schema(name)}), out / f"{name}.parquet"
            )
        counts[name] = len(records)
    return counts


def _consume(bundle: dict[str, Any], rows: dict[str, list[dict[str, Any]]]) -> None:
    entries = bundle.get("entry", [])
    for entry in entries:
        resource = entry.get("resource", {})
        kind = resource.get("resourceType")
        if kind == "Patient":
            rows["person"].append(_person(resource))
        elif kind == "Encounter":
            rows["visit_occurrence"].append(_visit(resource))
        elif kind == "Condition":
            rows["condition_occurrence"].append(_condition(resource))
        elif kind == "Procedure":
            rows["procedure_occurrence"].append(_procedure(resource))
        elif kind == "MedicationRequest":
            rows["drug_exposure"].append(_drug(resource))
        elif kind == "Coverage":
            rows["payer_plan_period"].append(_coverage(resource))


def _person(resource: dict[str, Any]) -> dict[str, Any]:
    return {
        "person_id": _id(resource),
        "gender_source_value": str(resource.get("gender") or ""),
        "birth_datetime": str(resource.get("birthDate") or ""),
        "race_source_value": "synthetic",
    }


def _visit(resource: dict[str, Any]) -> dict[str, Any]:
    period = resource.get("period") or {}
    return {
        "visit_occurrence_id": _id(resource),
        "person_id": _ref(resource.get("subject")),
        "visit_start_date": str(period.get("start") or "")[:10],
        "visit_end_date": str(period.get("end") or period.get("start") or "")[:10],
        "visit_source_value": str((resource.get("class") or {}).get("code") or ""),
    }


def _condition(resource: dict[str, Any]) -> dict[str, Any]:
    code, display = _code(resource.get("code"))
    return {
        "condition_occurrence_id": _id(resource),
        "person_id": _ref(resource.get("subject")),
        "visit_occurrence_id": _ref(resource.get("encounter")),
        "condition_start_date": str(resource.get("onsetDateTime") or "")[:10],
        "condition_source_value": code,
        "condition_source_concept_name": display,
    }


def _procedure(resource: dict[str, Any]) -> dict[str, Any]:
    code, display = _code(resource.get("code"))
    return {
        "procedure_occurrence_id": _id(resource),
        "person_id": _ref(resource.get("subject")),
        "visit_occurrence_id": _ref(resource.get("encounter")),
        "procedure_date": str(resource.get("performedDateTime") or "")[:10],
        "procedure_source_value": code,
        "procedure_source_concept_name": display,
    }


def _drug(resource: dict[str, Any]) -> dict[str, Any]:
    code, display = _code(resource.get("medicationCodeableConcept"))
    return {
        "drug_exposure_id": _id(resource),
        "person_id": _ref(resource.get("subject")),
        "visit_occurrence_id": _ref(resource.get("encounter")),
        "drug_exposure_start_date": str(resource.get("authoredOn") or "")[:10],
        "drug_source_value": code,
        "drug_source_concept_name": display,
    }


def _coverage(resource: dict[str, Any]) -> dict[str, Any]:
    period = resource.get("period") or {}
    classes = resource.get("class") or [{}]
    plan = ""
    for item in classes:
        if (
            item.get("type", {}).get("coding", [{}])[0].get("code") in {"plan", "group"}
            or "plan" in str(item.get("name", "")).lower()
        ):
            plan = str(item.get("value") or item.get("name") or "")
    payor = ""
    for party in resource.get("payor", []):
        payor = str(party.get("display") or party.get("reference") or "")
    return {
        "payer_plan_period_id": _id(resource),
        "person_id": _ref(resource.get("beneficiary")),
        "payer_source_value": payor,
        "plan_source_value": plan or "HMO Blue",
        "payer_plan_period_start_date": str(period.get("start") or "")[:10],
        "payer_plan_period_end_date": str(period.get("end") or "2099-01-01")[:10],
    }


def _id(resource: dict[str, Any]) -> str:
    return str(resource.get("id") or "")


def _ref(value: object) -> str:
    if not isinstance(value, dict):
        return ""
    reference = str(value.get("reference") or "")
    return reference.split("/")[-1]


def _code(value: object) -> tuple[str, str]:
    if not isinstance(value, dict):
        return "", ""
    coding = (value.get("coding") or [{}])[0]
    return str(coding.get("code") or ""), str(coding.get("display") or value.get("text") or "")


def _empty_schema(name: str) -> dict[str, list[Any]]:
    columns = {
        "person": ["person_id", "gender_source_value", "birth_datetime", "race_source_value"],
        "visit_occurrence": [
            "visit_occurrence_id",
            "person_id",
            "visit_start_date",
            "visit_end_date",
            "visit_source_value",
        ],
        "condition_occurrence": [
            "condition_occurrence_id",
            "person_id",
            "visit_occurrence_id",
            "condition_start_date",
            "condition_source_value",
            "condition_source_concept_name",
        ],
        "procedure_occurrence": [
            "procedure_occurrence_id",
            "person_id",
            "visit_occurrence_id",
            "procedure_date",
            "procedure_source_value",
            "procedure_source_concept_name",
        ],
        "drug_exposure": [
            "drug_exposure_id",
            "person_id",
            "visit_occurrence_id",
            "drug_exposure_start_date",
            "drug_source_value",
            "drug_source_concept_name",
        ],
        "payer_plan_period": [
            "payer_plan_period_id",
            "person_id",
            "payer_source_value",
            "plan_source_value",
            "payer_plan_period_start_date",
            "payer_plan_period_end_date",
        ],
    }
    return {column: [] for column in columns[name]}
