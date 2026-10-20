"""Download public policy documents listed in the source registry."""

from __future__ import annotations

from pathlib import Path

import httpx

from caretrace.config import Settings, get_settings
from caretrace.ingest.registry import SourceRegistry, load_registry


def fetch_policies(
    registry: SourceRegistry | None = None,
    *,
    settings: Settings | None = None,
    client: httpx.Client | None = None,
) -> list[Path]:
    """Save policy URLs under data/raw/policies. Local sample files are copied as-is."""
    cfg = settings or get_settings()
    reg = registry or load_registry()
    dest = cfg.raw_dir / "policies"
    dest.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    owns = client is None
    http = client or httpx.Client(timeout=30, headers={"User-Agent": cfg.user_agent})
    try:
        for payer in reg.payers:
            for url in payer.policy_urls:
                target = dest / f"{payer.payer_id}-{abs(hash(url)) % 10_000_000}.txt"
                if url.startswith("file://") or url.startswith("/"):
                    source = Path(url[7:] if url.startswith("file://") else url)
                    target.write_bytes(source.read_bytes())
                else:
                    try:
                        response = http.get(url, follow_redirects=True)
                        response.raise_for_status()
                        target.write_bytes(response.content)
                    except httpx.HTTPError:
                        continue
                written.append(target)
    finally:
        if owns:
            http.close()
    sample = Path("data/samples/policies/bcbsma_imaging.txt")
    if sample.exists():
        copy = dest / "bcbsma-sample.txt"
        copy.write_text(sample.read_text(encoding="utf-8"), encoding="utf-8")
        written.append(copy)
    return written
