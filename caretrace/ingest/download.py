"""Stream hospital MRF downloads with retries, resume, and content hashing."""

from __future__ import annotations

import gzip
import hashlib
import shutil
import time
import zipfile
from pathlib import Path

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from caretrace.config import Settings, get_settings
from caretrace.ingest.manifest import connect, record_download, unchanged
from caretrace.logging import get_logger

log = get_logger("download")


class FileTooLarge(Exception):
    """The remote file is larger than the caller's byte cap."""

    def __init__(self, size: int) -> None:
        self.size = size
        super().__init__(f"file is {size} bytes")


INDEX_SQL = """
SELECT source_id, location_name, mrf_url
FROM mrf_index
WHERE mrf_url IS NOT NULL AND mrf_url <> ''
"""


class HostRateLimiter:
    """At most one request per second per host, unless min_interval is 0."""

    def __init__(self, min_interval: float = 1.0) -> None:
        self.min_interval = min_interval
        self._last: dict[str, float] = {}

    def wait(self, host: str) -> None:
        if self.min_interval <= 0:
            return
        now = time.monotonic()
        last = self._last.get(host, 0.0)
        delay = self.min_interval - (now - last)
        if delay > 0:
            time.sleep(delay)
        self._last[host] = time.monotonic()


def file_id_for(source_id: str, uri: str) -> str:
    """Stable id for a source URL."""
    return hashlib.sha256(f"{source_id}|{uri}".encode()).hexdigest()[:16]


def _dest_for(settings: Settings, source_id: str, file_id: str, uri: str) -> Path:
    suffix = Path(uri.split("?", 1)[0]).suffix.lower() or ".bin"
    if suffix not in {".json", ".csv", ".zip", ".gz", ".txt"}:
        suffix = ".bin"
    directory = settings.raw_dir / source_id
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{file_id}{suffix}"


@retry(
    retry=retry_if_exception_type(httpx.HTTPError),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.2, min=0.2, max=2),
    reraise=True,
)
def _stream_once(
    client: httpx.Client,
    uri: str,
    dest: Path,
    limiter: HostRateLimiter,
    max_bytes: int | None = None,
) -> tuple[str, int]:
    """Download uri to dest. Resume with Range when a partial file exists."""
    host = httpx.URL(uri).host or "local"
    limiter.wait(host)
    partial = dest.with_suffix(dest.suffix + ".part")
    headers: dict[str, str] = {}
    mode = "wb"
    hasher = hashlib.sha256()
    size = 0
    if partial.exists() and partial.stat().st_size > 0:
        start = partial.stat().st_size
        headers["Range"] = f"bytes={start}-"
        mode = "ab"
        # Re-hash the bytes already on disk so the final digest covers the whole file.
        with partial.open("rb") as existing:
            while True:
                chunk = existing.read(1024 * 1024)
                if not chunk:
                    break
                hasher.update(chunk)
                size += len(chunk)
    with client.stream("GET", uri, headers=headers, follow_redirects=True) as response:
        if response.status_code == 416:
            # The partial file is already complete.
            pass
        else:
            response.raise_for_status()
            if mode == "ab" and response.status_code != 206:
                mode = "wb"
                hasher = hashlib.sha256()
                size = 0
                partial.unlink(missing_ok=True)
            declared = response.headers.get("content-length")
            if max_bytes is not None and declared is not None and declared.isdigit():
                incoming = int(declared)
                total = size + incoming if response.status_code == 206 else incoming
                if total > max_bytes:
                    raise FileTooLarge(total)
            with partial.open(mode) as handle:
                for chunk in response.iter_bytes():
                    if max_bytes is not None and size + len(chunk) > max_bytes:
                        raise FileTooLarge(size + len(chunk))
                    handle.write(chunk)
                    hasher.update(chunk)
                    size += len(chunk)
    partial.replace(dest)
    return hasher.hexdigest(), size


def _materialize(path: Path, source_dir: Path) -> list[Path]:
    """Decompress gzip or unzip into the source directory. Return parseable files."""
    name = path.name.lower()
    if name.endswith(".gz") and not name.endswith(".tar.gz"):
        target = source_dir / path.name[:-3]
        with gzip.open(path, "rb") as src, target.open("wb") as dst:
            shutil.copyfileobj(src, dst)
        return [target]
    if name.endswith(".zip"):
        extracted: list[Path] = []
        with zipfile.ZipFile(path) as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                target = source_dir / f"{path.stem}_{Path(info.filename).name}"
                with archive.open(info) as src, target.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
                extracted.append(target)
        return extracted
    return [path]


def download_one(
    source_id: str,
    uri: str,
    *,
    settings: Settings | None = None,
    client: httpx.Client | None = None,
    limiter: HostRateLimiter | None = None,
    max_bytes: int | None = None,
) -> dict[str, object]:
    """Stream one MRF. Skip the transfer when the stored sha256 still matches."""
    cfg = settings or get_settings()
    fid = file_id_for(source_id, uri)
    dest = _dest_for(cfg, source_id, fid, uri)
    owns = client is None
    http = client or httpx.Client(headers={"User-Agent": cfg.user_agent}, timeout=60.0)
    gate = limiter or HostRateLimiter(0.0 if client is not None else 1.0)
    try:
        if dest.exists():
            digest = hashlib.sha256(dest.read_bytes()).hexdigest()
            if unchanged(fid, digest, cfg):
                log.info("download_skipped", file_id=fid, source_id=source_id)
                return {"file_id": fid, "path": str(dest), "skipped": True, "sha256": digest}
        try:
            digest, size = _stream_once(http, uri, dest, gate, max_bytes)
        except FileTooLarge as exc:
            dest.unlink(missing_ok=True)
            dest.with_suffix(dest.suffix + ".part").unlink(missing_ok=True)
            record_download(
                fid,
                source_id,
                uri,
                "",
                exc.size,
                status="error",
                error="max_bytes",
                settings=cfg,
            )
            return {"file_id": fid, "skipped": False, "error": "max_bytes", "bytes": exc.size}
        if unchanged(fid, digest, cfg):
            return {"file_id": fid, "path": str(dest), "skipped": True, "sha256": digest}
        produced = _materialize(dest, cfg.raw_dir / source_id)
        record_download(fid, source_id, uri, digest, size, settings=cfg)
        return {
            "file_id": fid,
            "path": str(dest),
            "extracted": [str(p) for p in produced],
            "skipped": False,
            "sha256": digest,
            "bytes": size,
        }
    except httpx.HTTPError as exc:
        record_download(fid, source_id, uri, "", 0, status="error", error=str(exc), settings=cfg)
        log.warning("download_failed", source_id=source_id, uri=uri, error=str(exc))
        return {"file_id": fid, "skipped": False, "error": str(exc)}
    finally:
        if owns:
            http.close()


def list_index(settings: Settings, source_id: str | None = None) -> list[tuple[str, str]]:
    """Return (source_id, mrf_url) pairs from mrf_index."""
    conn = connect(settings)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS mrf_index (
              source_id VARCHAR, location_name VARCHAR, source_page_url VARCHAR,
              mrf_url VARCHAR, contact_name VARCHAR, contact_email VARCHAR, discovered_at TIMESTAMP
            )
            """
        )
        if source_id:
            rows = conn.execute(
                "SELECT source_id, mrf_url FROM mrf_index WHERE source_id = ?",
                [source_id],
            ).fetchall()
        else:
            rows = conn.execute("SELECT source_id, mrf_url FROM mrf_index").fetchall()
        return [(str(r[0]), str(r[1])) for r in rows if r[1]]
    finally:
        conn.close()


def download(
    *,
    settings: Settings | None = None,
    source_id: str | None = None,
    max_gb: float | None = None,
    client: httpx.Client | None = None,
    limiter: HostRateLimiter | None = None,
) -> list[dict[str, object]]:
    """Download every indexed MRF. max_gb caps each file, not the batch."""
    cfg = settings or get_settings()
    max_bytes = int(max_gb * (1024**3)) if max_gb is not None else None
    results = []
    for sid, uri in list_index(cfg, source_id):
        if uri.startswith("file://") or uri.startswith("/"):
            # Local sample paths are copied rather than fetched.
            results.append(_copy_local(sid, uri, cfg))
            continue
        results.append(
            download_one(
                sid, uri, settings=cfg, client=client, limiter=limiter, max_bytes=max_bytes
            )
        )
    return results


def _copy_local(source_id: str, uri: str, settings: Settings) -> dict[str, object]:
    path = Path(uri[7:]) if uri.startswith("file://") else Path(uri)
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    fid = file_id_for(source_id, uri)
    if unchanged(fid, digest, settings):
        return {"file_id": fid, "skipped": True, "sha256": digest, "path": str(path)}
    dest = _dest_for(settings, source_id, fid, path.name)
    dest.write_bytes(data)
    produced = _materialize(dest, settings.raw_dir / source_id)
    record_download(fid, source_id, uri, digest, len(data), settings=settings)
    return {
        "file_id": fid,
        "skipped": False,
        "sha256": digest,
        "extracted": [str(p) for p in produced],
    }
