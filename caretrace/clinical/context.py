"""Patient context for the agent and the audit plausibility check."""

from __future__ import annotations

import duckdb

from caretrace.config import Settings, get_settings


def get_patient_context(person_id: str, settings: Settings | None = None) -> dict[str, object]:
    """Current plan, recent procedures, and conditions for one synthetic person."""
    cfg = settings or get_settings()
    root = cfg.bronze_dir / "omop"
    conn = duckdb.connect()
    try:
        coverage = conn.execute(
            f"""
            SELECT payer_source_value, plan_source_value,
                   payer_plan_period_start_date, payer_plan_period_end_date
            FROM read_parquet('{(root / "payer_plan_period" / "*.parquet").as_posix()}')
            WHERE person_id = ?
            ORDER BY payer_plan_period_end_date DESC
            LIMIT 1
            """,
            [person_id],
        ).fetchone()
        procedures = conn.execute(
            f"""
            SELECT procedure_date, procedure_source_value, procedure_source_concept_name
            FROM read_parquet('{(root / "procedure_occurrence" / "*.parquet").as_posix()}')
            WHERE person_id = ?
            ORDER BY procedure_date DESC
            """,
            [person_id],
        ).fetchall()
        conditions = conn.execute(
            f"""
            SELECT condition_start_date, condition_source_value, condition_source_concept_name
            FROM read_parquet('{(root / "condition_occurrence" / "*.parquet").as_posix()}')
            WHERE person_id = ?
            """,
            [person_id],
        ).fetchall()
    finally:
        conn.close()
    payer = coverage[0] if coverage else None
    plan = coverage[1] if coverage else None
    return {
        "person_id": person_id,
        "payer_id": _payer_id(str(payer or "")),
        "payer_name": payer,
        "plan": plan,
        "period": [coverage[2], coverage[3]] if coverage else None,
        "procedures": [{"date": row[0], "code": row[1], "label": row[2]} for row in procedures],
        "conditions": [{"date": row[0], "code": row[1], "label": row[2]} for row in conditions],
        "visit_complexity": "low" if conditions else "unknown",
    }


def _payer_id(name: str) -> str | None:
    lowered = name.lower()
    if "blue cross" in lowered or "bcbs" in lowered:
        return "bcbsma"
    if "harvard" in lowered:
        return "harvard-pilgrim"
    if "tufts" in lowered:
        return "tufts-health"
    if name in {"bcbsma", "harvard-pilgrim", "tufts-health"}:
        return name
    return None
