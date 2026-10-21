"""Repair hospital CSVs that leave an inch-mark quote unescaped."""

from __future__ import annotations

import re
from pathlib import Path

# Digit, then a lone quote, then a letter, space, or '(': 9"X, 4"X 5" (, 10" (.
# Use a literal space, not \\s — a field-closing quote before \\r\\n must stay.
# Do not match a field-closing quote before a comma (498.00","payer").
_INCH = re.compile(rb'(?<=\d)"(?=[A-Za-z( ])')


def csv_path_for_parse(path: Path) -> Path:
    """Return a CSV Polars can scan.

    Boston Medical Center and Brigham rows write inch marks such as ``9"X``
    or ``4"X 5" (`` inside a quoted description without doubling every quote.
    Doubling those marks makes the row valid. Files with no such mark are
    returned unchanged.
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
