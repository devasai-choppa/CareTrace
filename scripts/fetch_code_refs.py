"""Refresh HCPCS Level II and MS-DRG description seeds from CMS public files.

CPT descriptors are not downloaded. The committed seeds are a small public subset
used for offline tests. This script replaces those seeds only when the download
succeeds.
"""

from __future__ import annotations

import csv
from pathlib import Path

import httpx

HCPCS_URL = "https://www.cms.gov/files/zip/january-2026-alpha-numeric-hcpcs-file.zip"
DRG_URL = "https://www.cms.gov/files/zip/ms-drg-v42-definitions-manual.zip"
SEED = Path("dbt/seeds")


def main() -> None:
    """Try the CMS downloads. Leave the committed seeds in place on failure."""
    SEED.mkdir(parents=True, exist_ok=True)
    ok = _try(HCPCS_URL, "hcpcs") and _try(DRG_URL, "ms-drg")
    if not ok:
        print("CMS download did not complete. Keeping the committed public-description seeds.")
        print(f"Current HCPCS rows: {_count(SEED / 'hcpcs_descriptions.csv')}")
        print(f"Current MS-DRG rows: {_count(SEED / 'ms_drg_descriptions.csv')}")


def _try(url: str, label: str) -> bool:
    try:
        response = httpx.get(url, timeout=30, follow_redirects=True)
    except httpx.HTTPError as exc:
        print(f"{label}: {exc}")
        return False
    print(f"{label}: HTTP {response.status_code}, {len(response.content)} bytes")
    return response.status_code == 200 and len(response.content) > 1000


def _count(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open(encoding="utf-8") as handle:
        return max(0, sum(1 for _ in csv.reader(handle)) - 1)


if __name__ == "__main__":
    main()
