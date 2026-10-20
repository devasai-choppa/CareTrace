"""Offline sample load: parse, resolve payers, rebuild serving marts."""

from __future__ import annotations

from pathlib import Path

from caretrace.config import Settings, get_settings
from caretrace.ingest.manifest import record_download
from caretrace.ingest.registry import load_registry
from caretrace.parse.payer_resolve import resolve_all
from caretrace.parse.runner import parse_file
from caretrace.warehouse import rebuild_marts

SAMPLES = [
    ("mgh", "sample-mgh", "mgh_mrf.json", "Massachusetts General Hospital"),
    ("bwh", "sample-bwh", "bwh_mrf_tall.csv", "Brigham and Women's Hospital"),
    ("bidmc", "sample-bidmc", "bidmc_mrf_wide.csv", "Beth Israel Deaconess Medical Center"),
    ("tufts", "sample-tufts", "tufts_mrf.json", "Tufts Medical Center"),
    ("bmc", "sample-bmc", "bmc_mrf.json", "Boston Medical Center"),
]


def load_samples(settings: Settings | None = None) -> dict[str, object]:
    """Parse the committed sample MRFs and build gold serving tables."""
    cfg = settings or get_settings()
    registry = load_registry()
    names = {item.source_id: item.name for item in registry.hospitals}
    stats = []
    for source_id, file_id, filename, fallback in SAMPLES:
        path = cfg.samples_dir / filename
        if not path.exists():
            path = Path("data/samples") / filename
        uri = str(path.resolve())
        record_download(
            file_id,
            source_id,
            uri,
            sha256=_sha(path),
            nbytes=path.stat().st_size,
            settings=cfg,
        )
        stats.append(
            parse_file(
                path,
                source_id=source_id,
                file_id=file_id,
                source_file_uri=uri,
                hospital_name=names.get(source_id, fallback),
                settings=cfg,
            )
        )
    resolutions = resolve_all(cfg, registry)
    counts = rebuild_marts(cfg, registry)
    _write_mrf_index(cfg)
    _write_clinical(cfg)
    _write_directory(cfg)
    return {"files": stats, "payers": len(resolutions), "marts": counts}


def _write_mrf_index(settings: Settings) -> None:
    from datetime import UTC, datetime

    import pyarrow as pa
    import pyarrow.parquet as pq

    rows = []
    for source_id, file_id, filename, _name in SAMPLES:
        path = settings.samples_dir / filename
        if not path.exists():
            path = Path("data/samples") / filename
        rows.append(
            {
                "source_id": source_id,
                "location_name": source_id,
                "source_page_url": "",
                "mrf_url": str(path),
                "contact_name": "",
                "contact_email": "",
                "discovered_at": datetime.now(UTC).isoformat(),
                "file_id": file_id,
            }
        )
    out = settings.bronze_dir / "mrf_index"
    out.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows), out / "mrf_index.parquet")


def _write_clinical(settings: Settings) -> None:
    from caretrace.clinical.fhir_to_omop import convert_bundles

    bundle = Path("data/samples/fhir/patient_bundle.json")
    if bundle.exists():
        convert_bundles([bundle], settings)


def _write_directory(settings: Settings) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    from caretrace.directory.payer_directory import MockDirectory
    from caretrace.directory.trust import score_entry

    rows = []
    for entry in MockDirectory().entries:
        scored = score_entry(entry)
        rows.append(
            {
                "payer_id": scored.payer_id,
                "npi": scored.npi,
                "provider_name": scored.provider_name,
                "score": scored.score,
                "ghost_risk": scored.ghost_risk,
                "reasons": "; ".join(scored.reasons),
            }
        )
    out = settings.bronze_dir / "directory_trust"
    out.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows), out / "directory_trust.parquet")


def _sha(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
