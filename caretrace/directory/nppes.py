"""NPPES NPI Registry client with a 30-day DuckDB cache."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from caretrace.config import Settings, get_settings
from caretrace.ingest.manifest import connect

TTL = timedelta(days=30)

DDL = """
CREATE TABLE IF NOT EXISTS nppes_cache (
  cache_key VARCHAR PRIMARY KEY,
  payload VARCHAR,
  fetched_at TIMESTAMP
)
"""


def lookup_npi(
    npi: str,
    *,
    settings: Settings | None = None,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Look up one NPI. Cached responses younger than 30 days are reused."""
    return _cached(
        f"npi:{npi}",
        {"version": "2.1", "number": npi},
        settings=settings,
        client=client,
    )


def lookup_name(
    first_name: str,
    last_name: str,
    state: str,
    taxonomy: str | None = None,
    *,
    settings: Settings | None = None,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Search NPPES by name, state, and optional taxonomy description."""
    params: dict[str, str] = {
        "version": "2.1",
        "first_name": first_name,
        "last_name": last_name,
        "state": state,
    }
    if taxonomy:
        params["taxonomy_description"] = taxonomy
    key = "name:" + "|".join(params.values())
    return _cached(key, params, settings=settings, client=client)


def _cached(
    key: str,
    params: dict[str, str],
    *,
    settings: Settings | None,
    client: httpx.Client | None,
) -> dict[str, Any]:
    cfg = settings or get_settings()
    conn = connect(cfg)
    try:
        conn.execute(DDL)
        row = conn.execute(
            "SELECT payload, fetched_at FROM nppes_cache WHERE cache_key = ?", [key]
        ).fetchone()
        if row is not None:
            fetched = row[1]
            if isinstance(fetched, datetime) and fetched.tzinfo is None:
                fetched = fetched.replace(tzinfo=UTC)
            if isinstance(fetched, datetime) and datetime.now(UTC) - fetched < TTL:
                loaded = json.loads(row[0])
                return loaded if isinstance(loaded, dict) else {"results": loaded}
        owns = client is None
        http = client or httpx.Client(timeout=30, headers={"User-Agent": cfg.user_agent})
        try:
            response = http.get(cfg.nppes_api_base, params=params)
            response.raise_for_status()
            payload = response.json()
        finally:
            if owns:
                http.close()
        conn.execute(
            "INSERT OR REPLACE INTO nppes_cache (cache_key, payload, fetched_at) VALUES (?, ?, ?)",
            [key, json.dumps(payload), datetime.now(UTC)],
        )
        return payload if isinstance(payload, dict) else {"results": payload}
    finally:
        conn.close()


def load_bulk_subset(records: list[dict[str, Any]], *, settings: Settings | None = None) -> int:
    """Cache a monthly NPPES extract. The MA subset is the supported demo slice."""
    cfg = settings or get_settings()
    conn = connect(cfg)
    try:
        conn.execute(DDL)
        for record in records:
            npi = str(record.get("number") or record.get("npi"))
            conn.execute(
                "INSERT OR REPLACE INTO nppes_cache (cache_key, payload, fetched_at) VALUES (?, ?, ?)",
                [
                    f"npi:{npi}",
                    json.dumps({"result_count": 1, "results": [record]}),
                    datetime.now(UTC),
                ],
            )
        return len(records)
    finally:
        conn.close()
