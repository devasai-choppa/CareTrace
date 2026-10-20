"""Write 30 synthetic EOB PDFs and an answer-key JSON file."""

from __future__ import annotations

from pathlib import Path

from caretrace.audit.synthetic import expanded_cases, write_suite


def main() -> None:
    """Generate the eval bill set under data/samples/eobs."""
    directory = Path("data/samples/eobs")
    paths = write_suite(directory, expanded_cases(30))
    print(f"wrote {len(paths)} synthetic EOBs to {directory}")


if __name__ == "__main__":
    main()
