"""Stream CMS JSON template v2 standard_charge_information arrays with ijson."""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import ijson

from caretrace.parse.common import (
    normalize_code_type,
    normalize_setting,
    parse_money,
    stamp_row,
    validate_row,
)


def _header_value(path: Path, key: str) -> str | None:
    """Read a top-level string without loading the charge array."""
    with path.open("rb") as handle:
        blob = handle.read(65536).decode("utf-8", errors="ignore")
    match = re.search(rf'"{key}"\s*:\s*"([^"]*)"', blob)
    return match.group(1) if match else None


def iter_rows(
    path: Path,
    *,
    source_id: str,
    file_id: str,
    source_file_uri: str,
) -> Iterator[tuple[dict[str, object], str | None]]:
    """Yield (row, quarantine_reason) for every exploded charge cell.

    Each code x setting x payer becomes one bronze row. `source_file_uri` is
    accepted so callers keep a uniform parser signature; the hash uses file_id.
    """
    del source_file_uri
    hospital_name = _header_value(path, "hospital_name")
    last_updated = _header_value(path, "last_updated_on")
    version = _header_value(path, "version")
    with path.open("rb") as handle:
        for item in ijson.items(handle, "standard_charge_information.item"):
            yield from _explode_item(
                item,
                source_id=source_id,
                file_id=file_id,
                hospital_name=hospital_name,
                last_updated=last_updated,
                version=version,
            )


def _explode_item(
    item: dict[str, object],
    *,
    source_id: str,
    file_id: str,
    hospital_name: str | None,
    last_updated: str | None,
    version: str | None,
) -> Iterator[tuple[dict[str, object], str | None]]:
    description = _text(item.get("description"))
    notes = _text(item.get("additional_generic_notes") or item.get("additional_notes"))
    codes_raw = item.get("code_information") or [{}]
    charges_raw = item.get("standard_charges") or [{}]
    codes: list[Any] = codes_raw if isinstance(codes_raw, list) else [{}]
    charges: list[Any] = charges_raw if isinstance(charges_raw, list) else [{}]
    for code_info in codes:
        info = code_info if isinstance(code_info, dict) else {}
        for charge in charges:
            cell = charge if isinstance(charge, dict) else {}
            payers = cell.get("payers_information") or [None]
            if not isinstance(payers, list):
                payers = [None]
            for payer in payers:
                payer_info = payer if isinstance(payer, dict) else {}
                row: dict[str, object] = {
                    "source_id": source_id,
                    "file_id": file_id,
                    "hospital_name": hospital_name,
                    "last_updated_on": last_updated,
                    "version": version,
                    "description": description,
                    "code": _text(info.get("code")),
                    "code_type": normalize_code_type(info.get("type")),
                    "setting": normalize_setting(cell.get("setting")),
                    "billing_class": _text(cell.get("billing_class")),
                    "gross_charge": parse_money(cell.get("gross_charge")),
                    "discounted_cash": parse_money(cell.get("discounted_cash")),
                    "payer_name_raw": _text(payer_info.get("payer_name")),
                    "plan_name_raw": _text(payer_info.get("plan_name")),
                    "negotiated_dollar": parse_money(
                        payer_info.get(
                            "standard_charge_dollar", payer_info.get("negotiated_dollar")
                        )
                    ),
                    "negotiated_percentage": parse_money(
                        payer_info.get(
                            "standard_charge_percentage", payer_info.get("negotiated_percentage")
                        )
                    ),
                    "negotiated_algorithm": _text(
                        payer_info.get(
                            "standard_charge_algorithm", payer_info.get("negotiated_algorithm")
                        )
                    ),
                    "estimated_amount": parse_money(payer_info.get("estimated_amount")),
                    "methodology": _text(payer_info.get("methodology")),
                    "min_negotiated": parse_money(cell.get("minimum")),
                    "max_negotiated": parse_money(cell.get("maximum")),
                    "additional_notes": notes or _text(cell.get("additional_generic_notes")),
                }
                reason = validate_row(row)
                yield stamp_row(row), reason


def _text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
