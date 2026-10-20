"""Parse a discovered MRF into partitioned bronze Parquet and parse_stats."""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from caretrace.config import Settings, get_settings
from caretrace.ingest.manifest import connect, mark_parsed
from caretrace.logging import get_logger
from caretrace.parse import csv_tall, csv_wide, json_v2
from caretrace.parse.common import ParquetBatchWriter
from caretrace.parse.detect import detect_format

log = get_logger("parse")

STATS_DDL = """
CREATE TABLE IF NOT EXISTS parse_stats (
  file_id VARCHAR,
  source_id VARCHAR,
  format VARCHAR,
  rows BIGINT,
  quarantined BIGINT,
  quarantine_pct DOUBLE,
  distinct_payers BIGINT,
  parsed_at TIMESTAMP
)
"""


def parse_file(
    path: Path,
    *,
    source_id: str,
    file_id: str,
    source_file_uri: str | None = None,
    hospital_name: str | None = None,
    settings: Settings | None = None,
) -> dict[str, object]:
    """Detect, stream, and write one MRF. Quarantine rows that fail validation."""
    cfg = settings or get_settings()
    uri = source_file_uri or str(path)
    fmt = detect_format(path)
    good_dir = cfg.bronze_dir / "standard_charges" / source_id / file_id
    bad_dir = cfg.bronze_dir / "_quarantine" / source_id / file_id
    shutil.rmtree(good_dir, ignore_errors=True)
    shutil.rmtree(bad_dir, ignore_errors=True)
    good = ParquetBatchWriter(good_dir)
    bad = ParquetBatchWriter(bad_dir, quarantine=True)
    payers: set[str] = set()
    try:
        if fmt == "json_v2":
            stream = json_v2.iter_rows(
                path, source_id=source_id, file_id=file_id, source_file_uri=uri
            )
        elif fmt == "csv_wide":
            stream = csv_wide.iter_rows(
                path,
                source_id=source_id,
                file_id=file_id,
                source_file_uri=uri,
                hospital_name=hospital_name,
            )
        elif fmt == "csv_tall":
            stream = csv_tall.iter_rows(
                path,
                source_id=source_id,
                file_id=file_id,
                source_file_uri=uri,
                hospital_name=hospital_name,
            )
        else:
            raise ValueError(f"unsupported MRF format: {fmt}")
        for row, reason in stream:
            payer = row.get("payer_name_raw")
            if isinstance(payer, str) and payer:
                payers.add(payer)
            if reason:
                row["reason"] = reason
                bad.add(row)
            else:
                good.add(row)
        rows = good.close()
        quarantined = bad.close()
    except Exception as exc:
        good.close()
        bad.close()
        mark_parsed(file_id, settings=cfg, error=str(exc))
        raise
    total = rows + quarantined
    stats = {
        "file_id": file_id,
        "source_id": source_id,
        "format": fmt,
        "rows": rows,
        "quarantined": quarantined,
        "quarantine_pct": (quarantined / total) if total else 0.0,
        "distinct_payers": len(payers),
        "parsed_at": datetime.now(UTC),
    }
    _record_stats(stats, cfg)
    mark_parsed(file_id, settings=cfg)
    log.info(
        "parsed", **{k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in stats.items()}
    )
    return stats


def _record_stats(stats: dict[str, object], settings: Settings) -> None:
    conn = connect(settings)
    try:
        conn.execute(STATS_DDL)
        conn.execute("DELETE FROM parse_stats WHERE file_id = ?", [stats["file_id"]])
        conn.execute(
            """
            INSERT INTO parse_stats
            (file_id, source_id, format, rows, quarantined, quarantine_pct, distinct_payers, parsed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                stats["file_id"],
                stats["source_id"],
                stats["format"],
                stats["rows"],
                stats["quarantined"],
                stats["quarantine_pct"],
                stats["distinct_payers"],
                stats["parsed_at"],
            ],
        )
        out = settings.bronze_dir / "parse_stats"
        out.mkdir(parents=True, exist_ok=True)
        conn.execute(
            f"COPY (SELECT * FROM parse_stats) TO '{out / 'parse_stats.parquet'}' (FORMAT PARQUET)"
        )
    finally:
        conn.close()


def read_bronze(settings: Settings | None = None) -> pa.Table:
    """Read every bronze standard_charges part into a pyarrow table (tests and marts)."""
    cfg = settings or get_settings()
    root = cfg.bronze_dir / "standard_charges"
    files = sorted(root.glob("**/*.parquet")) if root.exists() else []
    if not files:
        return pa.table({})
    return pq.read_table(files)
