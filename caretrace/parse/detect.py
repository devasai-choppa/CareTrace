"""Sniff an MRF as JSON v2, CSV tall, CSV wide, or unknown without a full read."""

from __future__ import annotations

from pathlib import Path


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
    first = text.splitlines()[0]
    lower = first.lower()
    if _is_wide(lower):
        return "csv_wide"
    if "payer_name" in lower or "code|1" in lower or "negotiated_dollar" in lower:
        return "csv_tall"
    if "," in first:
        return "csv_tall"
    return "unknown"


def _is_wide(header: str) -> bool:
    for column in header.split(","):
        name = column.strip().strip('"')
        if name.startswith("standard_charge|") and name.count("|") >= 3:
            return True
    return False
