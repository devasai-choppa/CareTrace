"""DuckDB ingest manifest: what was downloaded and whether it was parsed."""

from __future__ import annotations

from datetime import UTC, datetime

import duckdb

from caretrace.config import Settings, get_settings

DDL = """
CREATE TABLE IF NOT EXISTS ingest_manifest (
  file_id VARCHAR PRIMARY KEY,
  source_id VARCHAR,
  uri VARCHAR,
  sha256 VARCHAR,
  bytes BIGINT,
  downloaded_at TIMESTAMP,
  status VARCHAR,
  error VARCHAR
)
"""


def connect(settings: Settings | None = None) -> duckdb.DuckDBPyConnection:
    """Open the local warehouse file, creating parent directories as needed."""
    cfg = settings or get_settings()
    cfg.duckdb_path.parent.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect(str(cfg.duckdb_path))
    conn.execute(DDL)
    return conn


def record_download(
    file_id: str,
    source_id: str,
    uri: str,
    sha256: str,
    nbytes: int,
    *,
    status: str = "downloaded",
    error: str | None = None,
    settings: Settings | None = None,
) -> None:
    """Insert or replace a manifest row after a download attempt."""
    conn = connect(settings)
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO ingest_manifest
            (file_id, source_id, uri, sha256, bytes, downloaded_at, status, error)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [file_id, source_id, uri, sha256, nbytes, datetime.now(UTC), status, error],
        )
    finally:
        conn.close()


def mark_parsed(
    file_id: str, *, settings: Settings | None = None, error: str | None = None
) -> None:
    """Mark a downloaded file as parsed, or record a parse error."""
    conn = connect(settings)
    try:
        status = "error" if error else "parsed"
        conn.execute(
            "UPDATE ingest_manifest SET status = ?, error = ? WHERE file_id = ?",
            [status, error, file_id],
        )
    finally:
        conn.close()


def list_pending(settings: Settings | None = None) -> list[dict[str, object]]:
    """Return files downloaded but not yet parsed."""
    conn = connect(settings)
    try:
        rows = conn.execute(
            """
            SELECT file_id, source_id, uri, sha256, bytes, downloaded_at, status, error
            FROM ingest_manifest
            WHERE status = 'downloaded'
            ORDER BY downloaded_at
            """
        ).fetchall()
        cols = [
            "file_id",
            "source_id",
            "uri",
            "sha256",
            "bytes",
            "downloaded_at",
            "status",
            "error",
        ]
        return [dict(zip(cols, row, strict=True)) for row in rows]
    finally:
        conn.close()


def get_file(file_id: str, settings: Settings | None = None) -> dict[str, object] | None:
    """Fetch one manifest row."""
    conn = connect(settings)
    try:
        row = conn.execute(
            """
            SELECT file_id, source_id, uri, sha256, bytes, downloaded_at, status, error
            FROM ingest_manifest WHERE file_id = ?
            """,
            [file_id],
        ).fetchone()
        if row is None:
            return None
        cols = [
            "file_id",
            "source_id",
            "uri",
            "sha256",
            "bytes",
            "downloaded_at",
            "status",
            "error",
        ]
        return dict(zip(cols, row, strict=True))
    finally:
        conn.close()


def unchanged(file_id: str, sha256: str, settings: Settings | None = None) -> bool:
    """True when this content hash is already stored for the file."""
    existing = get_file(file_id, settings)
    if not existing:
        return False
    return existing["sha256"] == sha256 and existing["status"] in {"downloaded", "parsed"}
