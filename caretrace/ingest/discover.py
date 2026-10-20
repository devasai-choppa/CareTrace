"""Discover hospital machine-readable file URLs from CMS cms-hpt.txt indexes."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx

from caretrace.config import Settings, get_settings
from caretrace.ingest.manifest import connect
from caretrace.ingest.registry import HospitalSource, SourceRegistry, load_registry
from caretrace.logging import get_logger

log = get_logger("discover")

INDEX_DDL = """
CREATE TABLE IF NOT EXISTS mrf_index (
  source_id VARCHAR,
  location_name VARCHAR,
  source_page_url VARCHAR,
  mrf_url VARCHAR,
  contact_name VARCHAR,
  contact_email VARCHAR,
  discovered_at TIMESTAMP
)
"""


def parse_cms_hpt(text: str) -> list[dict[str, str]]:
    """Parse a cms-hpt.txt body into location blocks.

    CMS publishes key/value lines. A blank line starts a new location. HTML
    responses are rejected so a soft-404 does not look like an index.
    """
    stripped = text.lstrip()
    if stripped.lower().startswith("<!doctype html") or stripped.lower().startswith("<html"):
        raise ValueError("cms-hpt.txt response was HTML, not a text index")
    blocks: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            if current:
                blocks.append(current)
                current = {}
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        current[key.strip().lower()] = value.strip()
    if current:
        blocks.append(current)
    return blocks


def _store(rows: list[dict[str, str]], settings: Settings) -> None:
    conn = connect(settings)
    try:
        conn.execute(INDEX_DDL)
        if rows:
            source_ids = sorted({row["source_id"] for row in rows})
            conn.execute(
                f"DELETE FROM mrf_index WHERE source_id IN ({', '.join('?' for _ in source_ids)})",
                source_ids,
            )
            conn.executemany(
                """
                INSERT INTO mrf_index
                (source_id, location_name, source_page_url, mrf_url, contact_name, contact_email, discovered_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    [
                        row["source_id"],
                        row.get("location-name", ""),
                        row.get("source-page-url", ""),
                        row.get("mrf-url", ""),
                        row.get("contact-name", ""),
                        row.get("contact-email", ""),
                        datetime.now(UTC),
                    ]
                    for row in rows
                ],
            )
    finally:
        conn.close()


def discover_hospital(
    hospital: HospitalSource,
    client: httpx.Client,
) -> list[dict[str, str]]:
    """Fetch one hospital index. Fall back to mrf_url_override on failure."""
    rows: list[dict[str, str]] = []
    root = hospital.website_root.rstrip("/")
    if root:
        url = f"{root}/cms-hpt.txt"
        try:
            response = client.get(url, follow_redirects=True)
            if response.status_code == 404:
                log.warning("cms_hpt_missing", source_id=hospital.source_id, url=url)
            else:
                response.raise_for_status()
                for block in parse_cms_hpt(response.text):
                    block["source_id"] = hospital.source_id
                    if block.get("mrf-url"):
                        rows.append(block)
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("cms_hpt_failed", source_id=hospital.source_id, error=str(exc))
    if not rows and hospital.mrf_url_override:
        rows.append(
            {
                "source_id": hospital.source_id,
                "location-name": hospital.name,
                "source-page-url": root,
                "mrf-url": hospital.mrf_url_override,
                "contact-name": "",
                "contact-email": "",
            }
        )
    return rows


def discover(
    registry: SourceRegistry | None = None,
    *,
    settings: Settings | None = None,
    client: httpx.Client | None = None,
    source_id: str | None = None,
) -> list[dict[str, str]]:
    """Discover MRF URLs for every configured hospital and store mrf_index."""
    cfg = settings or get_settings()
    reg = registry or load_registry()
    owns_client = client is None
    http = client or httpx.Client(
        headers={"User-Agent": cfg.user_agent}, timeout=30.0, follow_redirects=True
    )
    found: list[dict[str, str]] = []
    try:
        for hospital in reg.hospitals:
            if source_id and hospital.source_id != source_id:
                continue
            found.extend(discover_hospital(hospital, http))
    finally:
        if owns_client:
            http.close()
    _store(found, cfg)
    return found
