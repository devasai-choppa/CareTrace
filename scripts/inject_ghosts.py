"""Write a labeled synthetic directory with known ghost listings."""

from __future__ import annotations

from pathlib import Path

from caretrace.directory.trust_model import inject_ghosts


def main() -> None:
    """Save the ghost-injection design matrix used by the trust-model eval."""
    frame, labels = inject_ghosts(500)
    frame = frame.copy()
    frame["ghost"] = labels
    path = Path("eval/datasets/ghost_directory.csv")
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False)
    print(f"wrote {len(frame)} rows to {path}")


if __name__ == "__main__":
    main()
