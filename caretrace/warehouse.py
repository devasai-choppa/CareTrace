"""Serving marts and the cited rate lookup used by the agent and API."""

from __future__ import annotations

from datetime import UTC, datetime

import duckdb
import pandas as pd

from caretrace.config import Settings, get_settings
from caretrace.ingest.manifest import connect
from caretrace.ingest.registry import SourceRegistry, load_registry
from caretrace.metrics import quality_reasons, quality_score, transparency_score
from caretrace.models.domain import RateQuote
from caretrace.models.lineage import Cited, Lineage
from caretrace.parse.common import PARSER_VERSION

ZIP_CITY = {
    "02111": "Boston",
    "02114": "Boston",
    "02115": "Boston",
    "02116": "Boston",
    "02118": "Boston",
    "02130": "Boston",
}


def city_for_zip(zip_code: str | None) -> str | None:
    """Map a demo ZIP to a city. Unknown ZIPs do not filter the result."""
    if not zip_code:
        return None
    return ZIP_CITY.get(zip_code.strip()[:5])


def _sql_path(path: str) -> str:
    return path.replace("'", "''")


def rebuild_marts(
    settings: Settings | None = None, registry: SourceRegistry | None = None
) -> dict[str, int]:
    """Build gold serving tables in DuckDB from bronze Parquet."""
    cfg = settings or get_settings()
    reg = registry or load_registry()
    bronze = cfg.bronze_dir / "standard_charges"
    files = list(bronze.glob("**/*.parquet")) if bronze.exists() else []
    if not files:
        raise FileNotFoundError("No bronze standard_charges parquet. Run caretrace load-samples.")
    payer_file = cfg.bronze_dir / "payer_resolution" / "payer_resolution.parquet"
    if not payer_file.exists():
        raise FileNotFoundError("payer_resolution parquet is missing.")
    conn = connect(cfg)
    try:
        conn.execute("CREATE SCHEMA IF NOT EXISTS gold")
        bronze_glob = _sql_path(str(bronze / "**" / "*.parquet"))
        payer_path = _sql_path(str(payer_file))
        conn.execute(
            f"""
            CREATE OR REPLACE TABLE gold._charges AS
            WITH raw AS (
              SELECT * FROM read_parquet('{bronze_glob}', union_by_name=true)
            ),
            resolved AS (
              SELECT
                c.*,
                CASE
                  WHEN r.method IN ('alias', 'fuzzy', 'confirmed', 'fellegi_sunter') THEN r.payer_id
                  ELSE NULL
                END AS payer_id
              FROM raw c
              LEFT JOIN read_parquet('{payer_path}') r
                ON c.payer_name_raw = r.payer_name_raw
            ),
            peer AS (
              SELECT code, payer_id,
                     quantile_cont(negotiated_dollar, 0.25) AS q1,
                     quantile_cont(negotiated_dollar, 0.75) AS q3,
                     count(*) AS peer_n
              FROM resolved
              WHERE negotiated_dollar IS NOT NULL AND payer_id IS NOT NULL
              GROUP BY 1, 2
            )
            SELECT
              s.*,
              CASE
                WHEN s.negotiated_dollar IS NOT NULL THEN 'dollar'
                WHEN s.negotiated_percentage IS NOT NULL THEN 'percent'
                WHEN s.negotiated_algorithm IS NOT NULL AND s.negotiated_algorithm <> '' THEN 'algorithm'
                ELSE 'missing'
              END AS rate_kind,
              (
                s.negotiated_dollar IS NULL
                AND s.negotiated_percentage IS NOT NULL
                AND (s.negotiated_algorithm IS NULL OR s.negotiated_algorithm = '')
              ) AS percent_only,
              (
                s.negotiated_dollar IS NULL
                AND s.negotiated_algorithm IS NOT NULL
                AND s.negotiated_algorithm <> ''
              ) AS algorithm_only,
              (
                s.negotiated_dollar IS NOT NULL
                AND p.peer_n >= 4
                AND s.negotiated_dollar > p.q3 + 3 * (p.q3 - p.q1)
              ) AS outlier
            FROM resolved s
            LEFT JOIN peer p ON s.code = p.code AND s.payer_id = p.payer_id
            """
        )
        frame = conn.execute("SELECT * FROM gold._charges").fetchdf()
        frame["quality_score"] = [
            quality_score(
                has_dollar=_is_number(value),
                percent_only=bool(percent) and not _is_number(value),
                algorithm_only=bool(algo) and not _is_number(value),
                outlier=bool(out) if out == out else False,
            )
            for value, percent, algo, out in zip(
                frame["negotiated_dollar"],
                frame["percent_only"],
                frame["algorithm_only"],
                frame["outlier"],
                strict=True,
            )
        ]
        conn.register("scored", frame)
        conn.execute(
            """
            CREATE OR REPLACE TABLE gold.mart_negotiated_rates AS
            SELECT * EXCLUDE (rn) FROM (
              SELECT
                hospital_name,
                source_id,
                file_id,
                code,
                code_type,
                setting,
                payer_id,
                payer_name_raw,
                plan_name_raw AS plan_name,
                negotiated_dollar AS rate_dollar,
                rate_kind,
                discounted_cash,
                gross_charge,
                last_updated_on,
                source_row_hash,
                parser_version,
                ingested_at,
                quality_score,
                false AS imputed,
                CAST(NULL AS DOUBLE) AS interval_low,
                CAST(NULL AS DOUBLE) AS interval_high,
                row_number() OVER (
                  PARTITION BY source_id, code, setting, payer_id, coalesce(plan_name_raw, '')
                  ORDER BY quality_score DESC, ingested_at DESC
                ) AS rn
              FROM scored
              WHERE payer_id IS NOT NULL
            )
            WHERE rn = 1
            """
        )
        conn.execute(
            """
            CREATE OR REPLACE TABLE gold.mart_price_variance AS
            SELECT
              code,
              payer_id,
              min(rate_dollar) AS min_rate,
              quantile_cont(rate_dollar, 0.25) AS p25,
              quantile_cont(rate_dollar, 0.50) AS median_rate,
              quantile_cont(rate_dollar, 0.75) AS p75,
              max(rate_dollar) AS max_rate,
              max(rate_dollar) / nullif(min(rate_dollar), 0) AS spread_ratio,
              count(DISTINCT source_id) AS n_hospitals
            FROM gold.mart_negotiated_rates
            WHERE rate_dollar IS NOT NULL
            GROUP BY 1, 2
            """
        )
        _compliance(conn, reg)
        counts = {
            "rates": _count(conn, "gold.mart_negotiated_rates"),
            "variance": _count(conn, "gold.mart_price_variance"),
            "hospitals": _count(conn, "gold.mart_hospital_compliance"),
        }
        return counts
    finally:
        conn.close()


def _count(conn: duckdb.DuckDBPyConnection, table: str) -> int:
    row = conn.execute(f"SELECT count(*) FROM {table}").fetchone()
    if row is None:
        return 0
    return int(row[0])


def _is_number(value: object) -> bool:
    if value is None or isinstance(value, bool):
        return False
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
    return number == number


def _compliance(conn: duckdb.DuckDBPyConnection, registry: SourceRegistry) -> None:
    rows = conn.execute(
        """
        SELECT source_id,
               any_value(hospital_name) AS hospital_name,
               max(last_updated_on) AS last_updated_on,
               count(*) AS n_rows,
               count(*) FILTER (
                 WHERE negotiated_dollar IS NOT NULL
                    OR negotiated_percentage IS NOT NULL
                    OR (negotiated_algorithm IS NOT NULL AND negotiated_algorithm <> '')
               ) AS n_negotiated,
               count(*) FILTER (WHERE negotiated_dollar IS NOT NULL) AS n_dollar,
               count(DISTINCT payer_id) FILTER (WHERE payer_id IS NOT NULL) AS n_payers
        FROM gold._charges
        GROUP BY 1
        """
    ).fetchall()
    stats: dict[str, tuple[float, int]] = {}
    try:
        for source_id, pct, nrows in conn.execute(
            """
            SELECT source_id, max(quarantine_pct), sum(rows)
            FROM parse_stats GROUP BY 1
            """
        ).fetchall():
            stats[str(source_id)] = (float(pct or 0), int(nrows or 0))
    except duckdb.CatalogException:
        stats = {}
    expected = max(1, len(registry.payers))
    today = datetime.now(UTC).date()
    payload = []
    for source_id, name, updated, _n_rows, n_neg, n_dollar, n_payers in rows:
        updated_text = str(updated or "")
        try:
            age = (today - datetime.fromisoformat(updated_text).date()).days
        except ValueError:
            age = 9999
        pct_dollar = (n_dollar / n_neg) if n_neg else 0.0
        coverage = (n_payers or 0) / expected
        quarantine_pct = stats.get(str(source_id), (0.0, 0))[0]
        score = transparency_score(age, pct_dollar, coverage, quarantine_pct)
        payload.append(
            {
                "source_id": source_id,
                "hospital_name": name,
                "file_age_days": age,
                "pct_dollar_rates": pct_dollar,
                "payer_coverage": coverage,
                "quarantine_pct": quarantine_pct,
                "transparency_score": score,
            }
        )
    if not payload:
        conn.execute(
            """
            CREATE OR REPLACE TABLE gold.mart_hospital_compliance (
              source_id VARCHAR, hospital_name VARCHAR, file_age_days INTEGER,
              pct_dollar_rates DOUBLE, payer_coverage DOUBLE, quarantine_pct DOUBLE,
              transparency_score DOUBLE
            )
            """
        )
        return
    conn.register("compliance_df", pd.DataFrame(payload))
    conn.execute(
        "CREATE OR REPLACE TABLE gold.mart_hospital_compliance AS SELECT * FROM compliance_df"
    )


def negotiated_resolution_rate(settings: Settings | None = None) -> float:
    """Share of negotiated bronze rows that received a payer_id."""
    cfg = settings or get_settings()
    conn = connect(cfg)
    try:
        row = conn.execute(
            """
            SELECT
              count(*) FILTER (
                WHERE negotiated_dollar IS NOT NULL
                   OR negotiated_percentage IS NOT NULL
                   OR (negotiated_algorithm IS NOT NULL AND negotiated_algorithm <> '')
              ) AS denom,
              count(*) FILTER (
                WHERE payer_id IS NOT NULL AND (
                  negotiated_dollar IS NOT NULL
                  OR negotiated_percentage IS NOT NULL
                  OR (negotiated_algorithm IS NOT NULL AND negotiated_algorithm <> '')
                )
              ) AS numer
            FROM gold._charges
            """
        ).fetchone()
        if row is None or not row[0]:
            return 0.0
        return float(row[1]) / float(row[0])
    finally:
        conn.close()


def get_rates(
    code: str,
    payer_id: str | None = None,
    *,
    zip_code: str | None = None,
    city: str | None = None,
    settings: Settings | None = None,
    registry: SourceRegistry | None = None,
) -> list[Cited[RateQuote]]:
    """Return cited negotiated rates for a code, optionally filtered by payer and place."""
    cfg = settings or get_settings()
    reg = registry or load_registry()
    place = city or city_for_zip(zip_code)
    allowed = None
    if place:
        allowed = {h.source_id for h in reg.hospitals if h.city.lower() == place.lower()}
    conn = connect(cfg)
    try:
        query = "SELECT * FROM gold.mart_negotiated_rates WHERE code = ?"
        params: list[object] = [code]
        if payer_id:
            query += " AND payer_id = ?"
            params.append(payer_id)
        rows = conn.execute(query, params).fetchdf()
    finally:
        conn.close()
    quotes: list[Cited[RateQuote]] = []
    manifest = _manifest_uris(cfg)
    for record in rows.to_dict(orient="records"):
        source_id = str(record["source_id"])
        if allowed is not None and source_id not in allowed:
            continue
        has_dollar = _is_number(record.get("rate_dollar"))
        percent_only = record.get("rate_kind") == "percent"
        algorithm_only = record.get("rate_kind") == "algorithm"
        reasons = quality_reasons(
            has_dollar=has_dollar,
            percent_only=percent_only,
            algorithm_only=algorithm_only,
            outlier=False,
        )
        score = float(record.get("quality_score") or 0)
        ingested = _parse_time(record.get("ingested_at"))
        uri = manifest.get(str(record.get("file_id")), f"mrf://{source_id}/{record.get('file_id')}")
        lineage = Lineage(
            source_id=source_id,
            source_file_uri=uri,
            source_row_hash=str(record.get("source_row_hash")),
            ingested_at=ingested,
            parser_version=str(record.get("parser_version") or PARSER_VERSION),
            method="published_mrf",
            parameters={"rate_kind": record.get("rate_kind")},
        )
        quote = RateQuote(
            hospital_name=str(record.get("hospital_name") or source_id),
            source_id=source_id,
            code=str(record["code"]),
            code_type=str(record.get("code_type") or "OTHER"),
            setting=str(record.get("setting") or "outpatient"),
            payer_id=None if record.get("payer_id") is None else str(record["payer_id"]),
            payer_name_raw=None
            if record.get("payer_name_raw") is None
            else str(record["payer_name_raw"]),
            plan_name=None if record.get("plan_name") is None else str(record["plan_name"]),
            rate_dollar=float(record["rate_dollar"]) if has_dollar else None,
            rate_kind=str(record.get("rate_kind") or "missing"),
            discounted_cash=float(record["discounted_cash"])
            if _is_number(record.get("discounted_cash"))
            else None,
            gross_charge=float(record["gross_charge"])
            if _is_number(record.get("gross_charge"))
            else None,
            last_updated_on=None
            if record.get("last_updated_on") is None
            else str(record["last_updated_on"]),
            quality_score=score,
            imputed=bool(record.get("imputed")),
            interval_low=float(record["interval_low"])
            if _is_number(record.get("interval_low"))
            else None,
            interval_high=float(record["interval_high"])
            if _is_number(record.get("interval_high"))
            else None,
            file_id=None if record.get("file_id") is None else str(record["file_id"]),
            source_row_hash=str(record.get("source_row_hash")),
            source_file_uri=uri,
        )
        quotes.append(
            Cited(value=quote, lineage=[lineage], confidence=score, confidence_reasons=reasons)
        )
    quotes.sort(key=lambda item: (item.value.rate_dollar is None, item.value.rate_dollar or 0))
    return quotes


def _manifest_uris(settings: Settings) -> dict[str, str]:
    conn = connect(settings)
    try:
        try:
            rows = conn.execute("SELECT file_id, uri FROM ingest_manifest").fetchall()
        except duckdb.CatalogException:
            return {}
        return {str(file_id): str(uri) for file_id, uri in rows}
    finally:
        conn.close()


def _parse_time(value: object) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    text = str(value or "")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(UTC)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def variance_table(settings: Settings | None = None) -> list[dict[str, object]]:
    """Return mart_price_variance rows for the dashboard."""
    cfg = settings or get_settings()
    conn = connect(cfg)
    try:
        frame = conn.execute("SELECT * FROM gold.mart_price_variance").fetchdf()
    finally:
        conn.close()
    return frame.to_dict(orient="records")


def compliance_table(settings: Settings | None = None) -> list[dict[str, object]]:
    """Return hospital transparency scores."""
    cfg = settings or get_settings()
    conn = connect(cfg)
    try:
        frame = conn.execute(
            "SELECT * FROM gold.mart_hospital_compliance ORDER BY transparency_score DESC"
        ).fetchdf()
    finally:
        conn.close()
    return frame.to_dict(orient="records")
