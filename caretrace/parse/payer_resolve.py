"""Resolve messy MRF payer strings to canonical payer ids.

Rapidfuzz is the baseline. Probabilistic linkage in caretrace.er can replace
rows above the review band; both write the same payer_resolution table.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
from rapidfuzz import fuzz

from caretrace.config import Settings, get_settings
from caretrace.ingest.registry import PayerSource, SourceRegistry, load_registry
from caretrace.logging import get_logger

log = get_logger("payer_resolve")

AUTO_THRESHOLD = 90
REVIEW_THRESHOLD = 75
LEGAL_TOKENS = {
    "inc",
    "incorporated",
    "llc",
    "corp",
    "corporation",
    "co",
    "company",
    "plan",
}


def normalize_payer(name: str) -> str:
    """Lowercase, strip punctuation, and drop legal and 'plan' tokens."""
    text = name.lower().replace("&", " and ")
    cleaned = []
    token = []
    for char in text:
        if char.isalnum():
            token.append(char)
        else:
            if token:
                cleaned.append("".join(token))
                token = []
    if token:
        cleaned.append("".join(token))
    return " ".join(part for part in cleaned if part not in LEGAL_TOKENS)


def _alias_map(payers: list[PayerSource]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for payer in payers:
        names = [payer.canonical_name, *payer.aliases]
        for name in names:
            mapping[normalize_payer(name)] = payer.payer_id
    return mapping


def resolve_payer_name(name: str, payers: list[PayerSource]) -> dict[str, object]:
    """Return payer_id, method, and score for one raw payer string."""
    aliases = _alias_map(payers)
    normalized = normalize_payer(name)
    if normalized in aliases:
        return {
            "payer_name_raw": name,
            "payer_id": aliases[normalized],
            "method": "alias",
            "score": 100.0,
            "normalized": normalized,
        }
    best_id = None
    best_score = 0.0
    for payer in payers:
        for candidate in [payer.canonical_name, *payer.aliases]:
            score = float(fuzz.token_set_ratio(normalized, normalize_payer(candidate)))
            if score > best_score:
                best_score = score
                best_id = payer.payer_id
    if best_score >= AUTO_THRESHOLD:
        method = "fuzzy"
        payer_id = best_id
    elif best_score >= REVIEW_THRESHOLD:
        method = "review"
        payer_id = None
    else:
        method = "unknown"
        payer_id = None
    return {
        "payer_name_raw": name,
        "payer_id": payer_id,
        "method": method,
        "score": best_score,
        "normalized": normalized,
        "suggested_payer_id": best_id,
    }


def load_confirmations(path: Path) -> dict[str, str]:
    """Read payer_name_raw,payer_id overrides appended by `caretrace payers confirm`."""
    if not path.exists():
        return {}
    import csv

    confirmed: dict[str, str] = {}
    with path.open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            raw = (row.get("payer_name_raw") or "").strip()
            payer_id = (row.get("payer_id") or "").strip()
            if raw and payer_id:
                confirmed[raw] = payer_id
    return confirmed


def append_confirmation(path: Path, payer_name_raw: str, payer_id: str) -> None:
    """Append a human confirmation to the seed file."""
    import csv

    path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not path.exists() or path.stat().st_size == 0
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["payer_name_raw", "payer_id"])
        if write_header:
            writer.writeheader()
        writer.writerow({"payer_name_raw": payer_name_raw, "payer_id": payer_id})


def distinct_payer_names(settings: Settings) -> list[str]:
    """Distinct raw payer names currently in bronze."""
    root = settings.bronze_dir / "standard_charges"
    files = list(root.glob("**/*.parquet")) if root.exists() else []
    if not files:
        return []
    glob = str(root / "**" / "*.parquet")
    conn = duckdb.connect()
    try:
        rows = conn.execute(
            f"""
            SELECT DISTINCT payer_name_raw
            FROM read_parquet('{glob}', union_by_name=true)
            WHERE payer_name_raw IS NOT NULL AND payer_name_raw <> ''
            """
        ).fetchall()
    finally:
        conn.close()
    return [str(row[0]) for row in rows]


def resolve_all(
    settings: Settings | None = None,
    registry: SourceRegistry | None = None,
    *,
    extra_names: list[str] | None = None,
) -> list[dict[str, object]]:
    """Resolve every bronze payer name and write payer_resolution parquet."""
    cfg = settings or get_settings()
    reg = registry or load_registry()
    names = distinct_payer_names(cfg)
    for name in extra_names or []:
        if name not in names:
            names.append(name)
    confirmations = load_confirmations(Path("dbt/seeds/payer_confirmations.csv"))
    rows: list[dict[str, object]] = []
    for name in names:
        if name in confirmations:
            row = {
                "payer_name_raw": name,
                "payer_id": confirmations[name],
                "method": "confirmed",
                "score": 100.0,
            }
        else:
            row = resolve_payer_name(name, reg.payers)
        rows.append(row)
    _write(rows, cfg)
    log.info("payers_resolved", n=len(rows), auto=sum(1 for r in rows if r.get("payer_id")))
    return rows


def review_band(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    """Names sitting between the review and auto thresholds."""
    return [row for row in rows if row.get("method") == "review"]


def _write(rows: list[dict[str, object]], settings: Settings) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    out = settings.bronze_dir / "payer_resolution"
    out.mkdir(parents=True, exist_ok=True)
    table = pa.table(
        {
            "payer_name_raw": [str(r["payer_name_raw"]) for r in rows],
            "payer_id": [None if r.get("payer_id") is None else str(r["payer_id"]) for r in rows],
            "method": [str(r["method"]) for r in rows],
            "score": [float(str(r["score"])) for r in rows],
        }
    )
    pq.write_table(table, out / "payer_resolution.parquet")
