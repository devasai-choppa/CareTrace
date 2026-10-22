"""Shared types, money parsing, and batched Parquet writes for MRF parsers."""

from __future__ import annotations

import math
import re
from datetime import UTC, datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from caretrace.models.lineage import row_hash

PARSER_VERSION = "mrf-1.0.0"

CODE_TYPES = {"CPT", "HCPCS", "MS-DRG", "APR-DRG", "RC", "NDC", "LOCAL", "OTHER"}
SETTINGS = {"inpatient", "outpatient", "both"}

BRONZE_FIELDS = [
    "source_id",
    "file_id",
    "hospital_name",
    "last_updated_on",
    "version",
    "description",
    "code",
    "code_type",
    "setting",
    "billing_class",
    "gross_charge",
    "discounted_cash",
    "payer_name_raw",
    "plan_name_raw",
    "negotiated_dollar",
    "negotiated_percentage",
    "negotiated_algorithm",
    "estimated_amount",
    "methodology",
    "min_negotiated",
    "max_negotiated",
    "additional_notes",
    "source_row_hash",
    "parser_version",
    "ingested_at",
]

HASH_FIELDS = [
    "source_id",
    "file_id",
    "description",
    "code",
    "code_type",
    "setting",
    "billing_class",
    "gross_charge",
    "discounted_cash",
    "payer_name_raw",
    "plan_name_raw",
    "negotiated_dollar",
    "negotiated_percentage",
    "negotiated_algorithm",
    "estimated_amount",
    "methodology",
    "min_negotiated",
    "max_negotiated",
]

_MONEY_RE = re.compile(r"[^0-9.\-]")


def parse_money(value: object) -> float | None:
    """Parse a charge cell. '$1,234.00' and 'N/A' are both expected in MRFs."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        if isinstance(value, float) and math.isnan(value):
            return None
        return float(value)
    text = str(value).strip()
    if text.lower() in {"", "n/a", "na", "none", "null", "-", "—"}:
        return None
    cleaned = _MONEY_RE.sub("", text.replace(",", ""))
    if cleaned in {"", ".", "-", "-."}:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def normalize_code_type(value: object) -> str:
    """Map publisher code-type labels onto the bronze enum."""
    text = str(value or "").strip().upper().replace("_", "-")
    text = re.sub(r"\s+", "-", text)
    aliases = {
        "CPT": "CPT",
        "CPT4": "CPT",
        "HCPCS": "HCPCS",
        "MS-DRG": "MS-DRG",
        "MSDRG": "MS-DRG",
        "APR-DRG": "APR-DRG",
        "APRDRG": "APR-DRG",
        "RC": "RC",
        "REV": "RC",
        "REVENUE": "RC",
        "NDC": "NDC",
        "LOCAL": "LOCAL",
        "CDM": "LOCAL",
    }
    return aliases.get(text, "OTHER" if text else "OTHER")


def normalize_setting(value: object) -> str | None:
    """Map setting labels to inpatient, outpatient, or both."""
    text = str(value or "").strip().lower()
    if text in {"inpatient", "ip"}:
        return "inpatient"
    if text in {"outpatient", "op"}:
        return "outpatient"
    if text in {"both", "ip/op", "inpatient/outpatient"}:
        return "both"
    return None


def bronze_schema(*, quarantine: bool = False) -> pa.Schema:
    """Arrow schema for standard_charges, plus a reason column when quarantining."""
    fields = [
        pa.field("source_id", pa.string()),
        pa.field("file_id", pa.string()),
        pa.field("hospital_name", pa.string()),
        pa.field("last_updated_on", pa.string()),
        pa.field("version", pa.string()),
        pa.field("description", pa.string()),
        pa.field("code", pa.string()),
        pa.field("code_type", pa.string()),
        pa.field("setting", pa.string()),
        pa.field("billing_class", pa.string()),
        pa.field("gross_charge", pa.float64()),
        pa.field("discounted_cash", pa.float64()),
        pa.field("payer_name_raw", pa.string()),
        pa.field("plan_name_raw", pa.string()),
        pa.field("negotiated_dollar", pa.float64()),
        pa.field("negotiated_percentage", pa.float64()),
        pa.field("negotiated_algorithm", pa.string()),
        pa.field("estimated_amount", pa.float64()),
        pa.field("methodology", pa.string()),
        pa.field("min_negotiated", pa.float64()),
        pa.field("max_negotiated", pa.float64()),
        pa.field("additional_notes", pa.string()),
        pa.field("source_row_hash", pa.string()),
        pa.field("parser_version", pa.string()),
        pa.field("ingested_at", pa.string()),
    ]
    if quarantine:
        fields.append(pa.field("reason", pa.string()))
    return pa.schema(fields)


class ParquetBatchWriter:
    """Write bronze rows in batches so a large MRF never sits fully in memory."""

    def __init__(
        self, directory: Path, *, quarantine: bool = False, batch_size: int = 100_000
    ) -> None:
        self.directory = directory
        self.batch_size = batch_size
        self.schema = bronze_schema(quarantine=quarantine)
        self._buffer: list[dict[str, object]] = []
        self._part = 0
        self.rows = 0
        directory.mkdir(parents=True, exist_ok=True)

    def add(self, row: dict[str, object]) -> None:
        filled = {field.name: row.get(field.name) for field in self.schema}
        self._buffer.append(filled)
        if len(self._buffer) >= self.batch_size:
            self.flush()

    def flush(self) -> None:
        if not self._buffer:
            return
        table = pa.Table.from_pylist(self._buffer, schema=self.schema)
        pq.write_table(table, self.directory / f"part-{self._part:04d}.parquet")
        self.rows += len(self._buffer)
        self._buffer.clear()
        self._part += 1

    def close(self) -> int:
        self.flush()
        return self.rows


def stamp_row(row: dict[str, object]) -> dict[str, object]:
    """Attach parser version, ingest time, and a stable row hash."""
    payload = {key: row.get(key) for key in HASH_FIELDS}
    row["source_row_hash"] = row_hash(payload)
    row["parser_version"] = PARSER_VERSION
    row["ingested_at"] = datetime.now(UTC).isoformat()
    return row


def validate_row(row: dict[str, object]) -> str | None:
    """Return a quarantine reason, or None when the row can enter bronze."""
    code = str(row.get("code") or "").strip()
    if not code:
        return "missing_code"
    if row.get("code_type") not in CODE_TYPES:
        return "bad_code_type"
    if row.get("setting") not in SETTINGS:
        return "bad_setting"
    for field in ("gross_charge", "discounted_cash", "negotiated_dollar", "estimated_amount"):
        value = row.get(field)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value < 0:
            return "negative_money"
    return None
