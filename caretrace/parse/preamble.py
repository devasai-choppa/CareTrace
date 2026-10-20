"""CMS CSV v2/v3 hospital-info block that sits above the charge table."""

from __future__ import annotations

import csv
from collections.abc import Iterator
from pathlib import Path


class _LineCounter:
    """Count physical lines a csv.reader consumes, including quoted breaks."""

    def __init__(self, handle: Iterator[str]) -> None:
        self._handle = handle
        self.lines = 0

    def __iter__(self) -> _LineCounter:
        return self

    def __next__(self) -> str:
        line = next(self._handle)
        self.lines += 1
        return line


def cms_csv_preamble(path: Path) -> tuple[dict[str, str], int, list[str]]:
    """Return hospital metadata, lines to skip, and the charge header.

    CMS CSV template v2 and v3 write a hospital-info header and one value row
    before the charge header. A file whose first field is already a charge
    column (the committed samples) skips nothing.
    """
    with path.open(newline="", encoding="utf-8-sig") as handle:
        counter = _LineCounter(handle)
        reader = csv.reader(counter)
        try:
            first = next(reader)
        except StopIteration:
            return {}, 0, []
        if not first or first[0].strip().lower() != "hospital_name":
            return {}, 0, [cell.strip() for cell in first]
        try:
            values = next(reader)
        except StopIteration:
            return {}, 0, []
        meta = {
            key.strip().lower(): value.strip()
            for key, value in zip(first, values, strict=False)
            if key.strip()
        }
        skip = counter.lines
        try:
            header = next(reader)
        except StopIteration:
            return meta, skip, []
        return meta, skip, [cell.strip() for cell in header]
