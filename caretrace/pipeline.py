"""Offline sample load and live hospital load into serving marts."""

from __future__ import annotations

import shutil
from pathlib import Path

from caretrace.config import Settings, get_settings
from caretrace.ingest.discover import discover
from caretrace.ingest.download import HostRateLimiter, _materialize, download, file_id_for
from caretrace.ingest.manifest import record_download
from caretrace.ingest.registry import load_registry
from caretrace.logging import get_logger
from caretrace.parse.payer_resolve import resolve_all
from caretrace.parse.runner import parse_file
from caretrace.reference import shoppable_services
from caretrace.warehouse import rebuild_marts

log = get_logger("pipeline")

SAMPLES = [
    ("mgh", "sample-mgh", "mgh_mrf.json", "Massachusetts General Hospital"),
    ("bwh", "sample-bwh", "bwh_mrf_tall.csv", "Brigham and Women's Hospital"),
    ("bidmc", "sample-bidmc", "bidmc_mrf_wide.csv", "Beth Israel Deaconess Medical Center"),
    ("tufts", "sample-tufts", "tufts_mrf.json", "Tufts Medical Center"),
    ("bmc", "sample-bmc", "bmc_mrf.json", "Boston Medical Center"),
]

# Live payer strings seen in the Boston CMS 3.0.0 files (2026-10-20 parse).
LIVE_BCBSMA_CONFIRMATIONS = [
    "BLUE CROSS BLUE SHIELD [110001]",
    "BCBS",
    "BCBS [6001]",
    "BLUE CROSS OF MA [100274]",
]


def load_samples(settings: Settings | None = None) -> dict[str, object]:
    """Parse the committed sample MRFs and build gold serving tables."""
    cfg = settings or get_settings()
    registry = load_registry()
    names = {item.source_id: item.name for item in registry.hospitals}
    _clear_charge_bronze(cfg)
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


def load_live(
    settings: Settings | None = None,
    *,
    source_id: str | None = None,
    max_gb: float | None = None,
    shoppable_only: bool = True,
    skip_download: bool = False,
) -> dict[str, object]:
    """Discover, download, and parse live hospital files into gold marts.

    Replaces sample bronze charge partitions so the estimate UI cites published
    dollars from the hospitals' machine-readable files. Clinical and directory
    demo tables still come from the committed samples.

    By default only shoppable seed codes are written to bronze so a multi-GB
    Tufts or BMC file does not fill the serving warehouse.
    """
    cfg = settings or get_settings()
    registry = load_registry()
    names = {item.source_id: item.name for item in registry.hospitals}
    allowlist = {row["code"] for row in shoppable_services()} if shoppable_only else None
    discovered = discover(source_id=source_id, registry=registry, settings=cfg)
    downloads: list[dict[str, object]] = []
    if not skip_download:
        downloads = download(
            settings=cfg,
            source_id=source_id,
            max_gb=max_gb,
            limiter=HostRateLimiter(1.0),
        )
    _clear_charge_bronze(cfg)
    stats: list[dict[str, object]] = []
    targets = [
        hospital.source_id
        for hospital in registry.hospitals
        if source_id is None or hospital.source_id == source_id
    ]
    for sid in targets:
        for path, file_id, uri in _live_parse_targets(cfg, sid):
            stats.append(
                parse_file(
                    path,
                    source_id=sid,
                    file_id=file_id,
                    source_file_uri=uri,
                    hospital_name=names.get(sid, sid),
                    settings=cfg,
                    code_allowlist=allowlist,
                )
            )
    if not stats:
        raise FileNotFoundError(
            "No downloaded charge files under data/raw/. "
            "Run caretrace discover && caretrace download, or omit --skip-download."
        )
    resolutions = resolve_all(cfg, registry, extra_names=list(LIVE_BCBSMA_CONFIRMATIONS))
    counts = rebuild_marts(cfg, registry, codes=allowlist)
    _write_clinical(cfg)
    _write_directory(cfg)
    log.info(
        "load_live_done",
        hospitals=len(targets),
        files=len(stats),
        discovered=len(discovered),
        downloads=len(downloads),
        marts=counts,
    )
    return {
        "discovered": len(discovered),
        "downloads": downloads,
        "files": stats,
        "payers": len(resolutions),
        "marts": counts,
        "shoppable_only": shoppable_only,
    }


def _live_parse_targets(settings: Settings, source_id: str) -> list[tuple[Path, str, str]]:
    """Return (local_path, file_id, source_file_uri) for each downloaded MRF."""
    from caretrace.ingest.download import list_index

    root = settings.raw_dir / source_id
    out: list[tuple[Path, str, str]] = []
    for sid, uri in list_index(settings, source_id):
        if sid != source_id:
            continue
        file_id = file_id_for(source_id, uri)
        path = _resolve_local_charge(root, file_id)
        if path is None:
            log.warning("live_file_missing", source_id=source_id, file_id=file_id, uri=uri)
            continue
        out.append((path, file_id, uri))
    return out


def _resolve_local_charge(root: Path, file_id: str) -> Path | None:
    """Find the CSV or JSON charge file for a download id under data/raw."""
    if not root.exists():
        return None
    direct = []
    for path in sorted(root.iterdir()):
        if not path.is_file() or path.name.startswith("._"):
            continue
        if path.name.startswith(file_id) and path.suffix.lower() in {".csv", ".json"}:
            direct.append(path)
    if direct:
        # Prefer CSV/JSON that are not the compressed download itself.
        return direct[0]
    for suffix in (".zip", ".gz", ".json", ".csv"):
        archive = root / f"{file_id}{suffix}"
        if not archive.exists():
            continue
        if archive.suffix.lower() in {".csv", ".json"}:
            return archive
        produced = _materialize(archive, root)
        for path in produced:
            if path.suffix.lower() in {".csv", ".json"} and not path.name.startswith("._"):
                return path
    return None


def _clear_charge_bronze(settings: Settings) -> None:
    """Drop charge partitions so sample and live rows do not share one mart."""
    for name in ("standard_charges", "_quarantine", "payer_resolution"):
        path = settings.bronze_dir / name
        if path.exists():
            shutil.rmtree(path)


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
