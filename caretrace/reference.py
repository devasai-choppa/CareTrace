"""Code lists CareTrace is allowed to store. CPT plain labels are written by us."""

from __future__ import annotations

import csv
from pathlib import Path

from rapidfuzz import fuzz


def _read(name: str) -> list[dict[str, str]]:
    path = Path("dbt/seeds") / name
    with path.open(encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def shoppable_services() -> list[dict[str, str]]:
    """Shoppable services with plain labels written by CareTrace."""
    return _read("shoppable_services.csv")


def unbundling_pairs() -> list[tuple[str, str]]:
    """Illustrative bundled pairs. This is not the full CMS NCCI table."""
    pairs = []
    for row in _read("unbundling_pairs.csv"):
        pairs.append((row["code_a"], row["code_b"]))
        pairs.append((row["code_b"], row["code_a"]))
    return pairs


def lookup_codes(text: str, limit: int = 5) -> list[dict[str, str | float]]:
    """Match plain language to shoppable codes. Returns scores, not guesses below 60."""
    query = text.lower()
    scored: list[dict[str, str | float]] = []
    for row in shoppable_services():
        label = row["plain_label"]
        score = float(fuzz.token_set_ratio(query, label.lower()))
        if row["code"] in text:
            score = max(score, 100.0)
        if score >= 60:
            scored.append(
                {
                    "code": row["code"],
                    "code_type": row["code_type"],
                    "plain_label": label,
                    "score": score,
                }
            )
    scored.sort(key=lambda item: float(item["score"]), reverse=True)
    return scored[:limit]
