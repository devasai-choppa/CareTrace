"""Batched reader for CMS 'tall' charge CSVs (one payer per row)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import polars as pl

from caretrace.parse.common import (
    normalize_code_type,
    normalize_setting,
    parse_money,
    stamp_row,
    validate_row,
)

COLUMN_MAP = {
    "description": "description",
    "code": "code",
    "code|1": "code",
    "code_type": "code_type",
    "code|1|type": "code_type",
    "setting": "setting",
    "billing_class": "billing_class",
    "gross_charge": "gross_charge",
    "standard_charge|gross": "gross_charge",
    "discounted_cash": "discounted_cash",
    "standard_charge|discounted_cash": "discounted_cash",
    "payer_name": "payer_name_raw",
    "payer_name_raw": "payer_name_raw",
    "plan_name": "plan_name_raw",
    "plan_name_raw": "plan_name_raw",
    "negotiated_dollar": "negotiated_dollar",
    "standard_charge|negotiated_dollar": "negotiated_dollar",
    "negotiated_percentage": "negotiated_percentage",
    "standard_charge|negotiated_percentage": "negotiated_percentage",
    "negotiated_algorithm": "negotiated_algorithm",
    "standard_charge|negotiated_algorithm": "negotiated_algorithm",
    "estimated_amount": "estimated_amount",
    "standard_charge|estimated_amount": "estimated_amount",
    "methodology": "methodology",
    "standard_charge|methodology": "methodology",
    "min_negotiated": "min_negotiated",
    "standard_charge|min": "min_negotiated",
    "max_negotiated": "max_negotiated",
    "standard_charge|max": "max_negotiated",
    "additional_notes": "additional_notes",
    "additional_generic_notes": "additional_notes",
    "hospital_name": "hospital_name",
    "last_updated_on": "last_updated_on",
    "version": "version",
}

MONEY = {
    "gross_charge",
    "discounted_cash",
    "negotiated_dollar",
    "negotiated_percentage",
    "estimated_amount",
    "min_negotiated",
    "max_negotiated",
}


def iter_rows(
    path: Path,
    *,
    source_id: str,
    file_id: str,
    source_file_uri: str,
    hospital_name: str | None = None,
    batch_size: int = 50_000,
) -> Iterator[tuple[dict[str, object], str | None]]:
    """Yield tall-CSV rows in Polars batches."""
    del source_file_uri
    frame_iter = pl.scan_csv(path, infer_schema=False).collect_batches(
        chunk_size=batch_size, engine="streaming"
    )
    for frame in frame_iter:
        lookup = {column.lower(): column for column in frame.columns}
        for record in frame.iter_rows(named=True):
            yield _from_record(record, lookup, source_id, file_id, hospital_name)


def _from_record(
    record: dict[str, object],
    lookup: dict[str, str],
    source_id: str,
    file_id: str,
    hospital_name: str | None,
) -> tuple[dict[str, object], str | None]:
    mapped: dict[str, object] = {}
    for key, target in COLUMN_MAP.items():
        column = lookup.get(key)
        if column is None:
            continue
        mapped[target] = record.get(column)
    row: dict[str, object] = {
        "source_id": source_id,
        "file_id": file_id,
        "hospital_name": _text(mapped.get("hospital_name")) or hospital_name,
        "last_updated_on": _text(mapped.get("last_updated_on")),
        "version": _text(mapped.get("version")) or "csv-tall",
        "description": _text(mapped.get("description")),
        "code": _text(mapped.get("code")),
        "code_type": normalize_code_type(mapped.get("code_type")),
        "setting": normalize_setting(mapped.get("setting")),
        "billing_class": _text(mapped.get("billing_class")),
        "payer_name_raw": _text(mapped.get("payer_name_raw")),
        "plan_name_raw": _text(mapped.get("plan_name_raw")),
        "negotiated_algorithm": _text(mapped.get("negotiated_algorithm")),
        "methodology": _text(mapped.get("methodology")),
        "additional_notes": _text(mapped.get("additional_notes")),
    }
    for field in MONEY:
        row[field] = parse_money(mapped.get(field))
    return stamp_row(row), validate_row(row)


def _text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
