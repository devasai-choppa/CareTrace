"""Demo-mode guards that reject synthetic-looking identifiers we still refuse to process."""

from __future__ import annotations

import re

SSN_PATTERN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
SSN_COMPACT = re.compile(r"(?<!\d)\d{9}(?!\d)")


class PhiRejectedError(ValueError):
    """Raised when demo mode sees an SSN-like pattern."""


def find_ssn(text: str) -> str | None:
    """Return the first SSN-like token, if any."""
    match = SSN_PATTERN.search(text)
    if match:
        return match.group(0)
    compact = SSN_COMPACT.search(text)
    if compact:
        return compact.group(0)
    return None


def assert_no_phi(text: str, *, demo_mode: bool = True) -> None:
    """Reject payloads that contain an SSN pattern when demo mode is on.

    CareTrace never accepts real PHI. The demo guard is a backstop for uploads
    and chat text, not a determination that a file is de-identified.
    """
    if not demo_mode:
        return
    if find_ssn(text):
        raise PhiRejectedError(
            "Demo mode rejected this upload because it contains an SSN-like pattern. "
            "Use Synthea, CMS Blue Button sandbox, or the synthetic EOBs in data/samples."
        )
