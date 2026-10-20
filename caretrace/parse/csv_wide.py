"""Melt CMS 'wide' charge CSVs so each payer|plan column becomes a row."""

from __future__ import annotations

import re
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
from caretrace.parse.csv_tall import COLUMN_MAP, MONEY, _text

WIDE_COL = re.compile(
    r"^standard_charge\|(?P<payer>[^|]+)\|(?P<plan>[^|]+)\|(?P<field>[a-z0-9_]+)$",
    re.IGNORECASE,
)


def iter_rows(
    path: Path,
    *,
    source_id: str,
    file_id: str,
    source_file_uri: str,
    hospital_name: str | None = None,
    batch_size: int = 20_000,
) -> Iterator[tuple[dict[str, object], str | None]]:
    """Yield one bronze row per wide payer/plan field group."""
    del source_file_uri
    frame_iter = pl.scan_csv(path, infer_schema=False).collect_batches(
        chunk_size=batch_size, engine="streaming"
    )
    for frame in frame_iter:
        groups = _payer_groups(frame.columns)
        for record in frame.iter_rows(named=True):
            base = _base(record, frame.columns)
            if not groups:
                yield _emit(base, {}, source_id, file_id, hospital_name)
                continue
            for (payer, plan), fields in groups.items():
                payer_fields = {field: record.get(column) for field, column in fields.items()}
                yield _emit(
                    base,
                    {"payer": payer, "plan": plan, **payer_fields},
                    source_id,
                    file_id,
                    hospital_name,
                )


def _payer_groups(columns: list[str]) -> dict[tuple[str, str], dict[str, str]]:
    groups: dict[tuple[str, str], dict[str, str]] = {}
    for column in columns:
        match = WIDE_COL.match(column.strip())
        if not match:
            continue
        key = (match.group("payer").strip(), match.group("plan").strip())
        groups.setdefault(key, {})[match.group("field").lower()] = column
    return groups


def _base(record: dict[str, object], columns: list[str]) -> dict[str, object]:
    lookup = {column.lower(): column for column in columns}
    mapped: dict[str, object] = {}
    for key, target in COLUMN_MAP.items():
        column = lookup.get(key)
        if column is not None:
            mapped[target] = record.get(column)
    return mapped


def _emit(
    base: dict[str, object],
    payer: dict[str, object],
    source_id: str,
    file_id: str,
    hospital_name: str | None,
) -> tuple[dict[str, object], str | None]:
    row: dict[str, object] = {
        "source_id": source_id,
        "file_id": file_id,
        "hospital_name": _text(base.get("hospital_name")) or hospital_name,
        "last_updated_on": _text(base.get("last_updated_on")),
        "version": _text(base.get("version")) or "csv-wide",
        "description": _text(base.get("description")),
        "code": _text(base.get("code")),
        "code_type": normalize_code_type(base.get("code_type")),
        "setting": normalize_setting(base.get("setting")),
        "billing_class": _text(base.get("billing_class")),
        "payer_name_raw": _text(payer.get("payer")),
        "plan_name_raw": _text(payer.get("plan")),
        "negotiated_algorithm": _text(payer.get("negotiated_algorithm")),
        "methodology": _text(payer.get("methodology") or base.get("methodology")),
        "additional_notes": _text(base.get("additional_notes")),
    }
    for field in MONEY:
        source = payer.get(field, base.get(field))
        row[field] = parse_money(source)
    return stamp_row(row), validate_row(row)
