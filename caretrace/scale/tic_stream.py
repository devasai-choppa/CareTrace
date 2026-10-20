"""Stream a Transparency-in-Coverage in-network file without loading it.

The same iterator is what a PySpark mapPartitions job would call on each
byte-range split. This module runs it locally with ijson and records a
throughput number for docs/SCALE.md.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path

import ijson


def iter_in_network(
    path: Path,
    *,
    allowed_npis: set[str],
    allowed_codes: set[str],
) -> Iterator[dict[str, object]]:
    """Yield negotiated rates whose NPI and code are in the keep-sets."""
    with path.open("rb") as handle:
        for item in ijson.items(handle, "in_network.item"):
            code = str(item.get("billing_code") or "")
            if allowed_codes and code not in allowed_codes:
                continue
            for rate_group in item.get("negotiated_rates") or []:
                npis = {
                    str(npi)
                    for npi in rate_group.get("provider_references") or rate_group.get("npis") or []
                }
                # Some files put NPIs on provider_groups inside the item.
                for group in rate_group.get("provider_groups") or []:
                    npis.update(str(npi) for npi in group.get("npi") or [])
                if allowed_npis and npis and npis.isdisjoint(allowed_npis):
                    continue
                for price in rate_group.get("negotiated_prices") or []:
                    yield {
                        "billing_code": code,
                        "npis": sorted(npis),
                        "negotiated_rate": price.get("negotiated_rate"),
                        "negotiated_type": price.get("negotiated_type"),
                    }


def write_sample_tic(path: Path, n_items: int = 2000) -> Path:
    """Write a small TiC-shaped file for the benchmark. Not a real payer file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        handle.write('{"in_network":[')
        for index in range(n_items):
            if index:
                handle.write(",")
            keep = index % 5 == 0
            npi = "1234567893" if keep else "9999999999"
            code = "73721" if keep else "00000"
            handle.write(
                "{"
                f'"billing_code":"{code}",'
                '"negotiated_rates":[{"provider_groups":[{"npi":["'
                + npi
                + '"]}],"negotiated_prices":[{"negotiated_rate":100.0,"negotiated_type":"negotiated"}]}]'
                "}"
            )
        handle.write("]}")
    return path


def benchmark(path: Path, allowed_npis: set[str], allowed_codes: set[str]) -> dict[str, float]:
    """Return rows per second and megabytes per second for one local file."""
    size_mb = path.stat().st_size / (1024 * 1024)
    start = time.perf_counter()
    rows = sum(
        1 for _ in iter_in_network(path, allowed_npis=allowed_npis, allowed_codes=allowed_codes)
    )
    elapsed = max(time.perf_counter() - start, 1e-6)
    return {
        "rows": float(rows),
        "seconds": elapsed,
        "mb": size_mb,
        "mb_per_second": size_mb / elapsed,
        "rows_per_second": rows / elapsed,
    }
