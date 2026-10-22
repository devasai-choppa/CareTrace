"""Export gold marts to CSV and parquet for Tableau or Power BI."""

from __future__ import annotations

from pathlib import Path

import duckdb
from caretrace.config import get_settings

TABLES = (
    "gold.mart_negotiated_rates",
    "gold.mart_price_variance",
    "gold.mart_hospital_compliance",
)


def main() -> None:
    """Copy serving marts into exports/. Run caretrace load-samples first."""
    settings = get_settings()
    out = Path("exports")
    out.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect(str(settings.duckdb_path), read_only=True)
    try:
        for table in TABLES:
            name = table.split(".")[-1]
            frame = conn.execute(f"SELECT * FROM {table}").fetchdf()
            frame.to_csv(out / f"{name}.csv", index=False)
            frame.to_parquet(out / f"{name}.parquet", index=False)
            print(f"{name}: {len(frame)} rows")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
