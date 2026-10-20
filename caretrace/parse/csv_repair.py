"""Repair hospital CSVs that leave an inch-mark quote unescaped."""

from __future__ import annotations

import re
from pathlib import Path

# A digit, a quote, then a letter: 9"X inside a quoted description.
_INCH = re.compile(rb'(?<=\d)"(?=[A-Za-z])')


def csv_path_for_parse(path: Path) -> Path:
    """Return a CSV Polars can scan.

    A few Boston Medical Center rows write ``9"X`` inside a quoted field
    without doubling the quote. Doubling that one quote makes the row valid.
    Files with no such mark are returned unchanged.
    """
    if not _needs_repair(path):
        return path
    dest = path.with_name(path.name + ".inchfix")
    with path.open("rb") as src, dest.open("wb") as out:
        for line in src:
            out.write(_INCH.sub(b'""', line))
    return dest


def _needs_repair(path: Path) -> bool:
    with path.open("rb") as handle:
        for line in handle:
            if _INCH.search(line):
                return True
    return False
