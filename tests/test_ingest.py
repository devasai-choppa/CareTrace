from __future__ import annotations

import io
import zipfile
from pathlib import Path

import httpx
import respx
from caretrace.ingest.discover import discover, parse_cms_hpt, select_location_blocks
from caretrace.ingest.download import HostRateLimiter, download_one
from caretrace.ingest.manifest import get_file
from caretrace.ingest.registry import HospitalSource, SourceRegistry

HPT = """
location-name: Main Campus
source-page-url: https://example.invalid/prices
mrf-url: https://example.invalid/mrf.json
contact-name: Price Desk
contact-email: prices@example.invalid

location-name: Satellite
source-page-url: https://example.invalid/prices
mrf-url: https://example.invalid/satellite.csv
contact-name: Price Desk
contact-email: prices@example.invalid
"""


def test_parse_multi_location() -> None:
    blocks = parse_cms_hpt(HPT)
    assert len(blocks) == 2
    assert blocks[1]["mrf-url"].endswith("satellite.csv")


def test_select_location_keeps_named_hospital() -> None:
    blocks = parse_cms_hpt(HPT)
    kept = select_location_blocks(blocks, "Satellite")
    assert [block["location-name"] for block in kept] == ["Satellite"]
    assert len(select_location_blocks(blocks, "Unlisted")) == 2


def test_select_location_folds_apostrophe() -> None:
    blocks = [
        {
            "location-name": "Brigham and Women\u2019s Hospital",
            "mrf-url": "https://example.invalid/bwh.zip",
        }
    ]
    kept = select_location_blocks(blocks, "Brigham and Women's Hospital")
    assert kept == blocks


def test_parse_rejects_html() -> None:
    import pytest

    with pytest.raises(ValueError):
        parse_cms_hpt("<html><body>nope</body></html>")


@respx.mock
def test_discover_writes_index(settings) -> None:
    respx.get("https://hospital.example/cms-hpt.txt").mock(
        return_value=httpx.Response(200, text=HPT)
    )
    registry = SourceRegistry(
        hospitals=[
            HospitalSource(
                source_id="mgh",
                name="MGH",
                city="Boston",
                state="MA",
                website_root="https://hospital.example",
            )
        ],
        payers=[],
    )
    rows = discover(registry, settings=settings)
    assert len(rows) == 2
    named = SourceRegistry(
        hospitals=[
            HospitalSource(
                source_id="sat",
                name="Satellite",
                city="Boston",
                state="MA",
                website_root="https://hospital.example",
            )
        ],
        payers=[],
    )
    matched = discover(named, settings=settings)
    assert len(matched) == 1
    assert matched[0]["mrf-url"].endswith("satellite.csv")


@respx.mock
@respx.mock
def test_download_rejects_over_cap(settings) -> None:
    respx.get("https://files.example/big.zip").mock(
        return_value=httpx.Response(200, content=b"x" * 1000, headers={"content-length": "1000"})
    )
    result = download_one(
        "mgh",
        "https://files.example/big.zip",
        settings=settings,
        client=httpx.Client(),
        limiter=HostRateLimiter(0),
        max_bytes=10,
    )
    assert result["error"] == "max_bytes"
    assert list((settings.raw_dir / "mgh").glob("*")) == []


@respx.mock
def test_download_zip_and_skip(settings) -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("charges.json", '{"hospital_name":"X"}')
    payload = buffer.getvalue()
    respx.get("https://files.example/mrf.zip").mock(
        return_value=httpx.Response(200, content=payload)
    )
    client = httpx.Client()
    limiter = HostRateLimiter(0)
    first = download_one(
        "mgh",
        "https://files.example/mrf.zip",
        settings=settings,
        client=client,
        limiter=limiter,
    )
    assert first["skipped"] is False
    extracted = first["extracted"]
    assert any(str(path).endswith(".json") for path in extracted)
    second = download_one(
        "mgh",
        "https://files.example/mrf.zip",
        settings=settings,
        client=client,
        limiter=limiter,
    )
    assert second["skipped"] is True
    stored = get_file(str(first["file_id"]), settings)
    assert stored is not None
    assert stored["sha256"] == first["sha256"]
    client.close()
    assert Path(str(extracted[0])).exists()
