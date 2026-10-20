"""Sniff an MRF as JSON v2, CSV tall, CSV wide, or unknown without a full read."""

from __future__ import annotations

from pathlib import Path

from caretrace.parse.preamble import cms_csv_preamble


def detect_format(path: Path) -> str:
    """Return json_v2, csv_tall, csv_wide, legacy, or unknown from the header bytes."""
    with path.open("rb") as handle:
        head = handle.read(8192)
    text = head.decode("utf-8", errors="ignore").lstrip("\ufeff \t\r\n")
    if not text:
        return "unknown"
    if text.startswith("{"):
        if "standard_charge_information" in text:
            return "json_v2"
        return "legacy"
    _meta, _skip, fields = cms_csv_preamble(path)
    if _is_wide(fields):
        return "csv_wide"
    lower = [field.lower() for field in fields]
    if any(name in lower for name in ("payer_name", "code|1", "negotiated_dollar")):
        return "csv_tall"
    joined = ",".join(fields).lower()
    if "negotiated_dollar" in joined or "code|1" in joined:
        return "csv_tall"
    if len(fields) > 1:
        return "csv_tall"
    return "unknown"


def _is_wide(fields: list[str]) -> bool:
    for name in fields:
        column = name.strip().strip('"')
        if column.startswith("standard_charge|") and column.count("|") >= 3:
            return True
    return False
