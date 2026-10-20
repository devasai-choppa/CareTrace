from __future__ import annotations

import pandas as pd
from caretrace.audit.anomaly import AnomalyDetector
from caretrace.audit.engine import run_audit
from caretrace.audit.rules.builtin import AuditContext
from caretrace.models.domain import EobLine, RateQuote


def test_anomaly_adds_recall_without_hurting_top_precision() -> None:
    rng_rows = []
    for _index in range(40):
        rng_rows.append(
            {
                "allowed_over_rate": 1.0,
                "allowed_over_peer": 1.0,
                "units_z": 1.0,
                "pair_rarity": 0.0,
                "em_gap": 0.0,
            }
        )
    frame = pd.DataFrame(rng_rows)
    detector = AnomalyDetector().fit(frame)
    quote = RateQuote(
        hospital_name="MGH",
        source_id="mgh",
        code="73721",
        code_type="CPT",
        setting="outpatient",
        rate_dollar=1400,
        discounted_cash=900,
        quality_score=0.9,
        source_row_hash="h",
        source_file_uri="mrf://mgh",
    )
    lines = [
        EobLine(
            line_no=1,
            code="73721",
            allowed=2200,
            service_date="2026-06-01",
            units=1,
            provider_npi="1",
        ),
        EobLine(
            line_no=2,
            code="73721",
            allowed=2200,
            service_date="2026-06-01",
            units=1,
            provider_npi="1",
        ),
        EobLine(line_no=3, code="99215", allowed=9000, units=12),
    ]
    rules_only = run_audit(lines, AuditContext(rates_by_code={"73721": quote}))
    extra = detector.findings_for_lines(
        lines,
        {"73721": 1400, "99215": 260},
        {"73721": 1400, "99215": 260},
        threshold=0.0,
    )
    both = run_audit(lines, AuditContext(rates_by_code={"73721": quote}), extra=extra)
    top_rules = [item.rule_id for item in rules_only["findings"][:5]]
    top_both = [item.rule_id for item in both["findings"][:5]]
    assert (
        top_rules == [item for item in top_both if item != "A1"][: len(top_rules)]
        or top_both[: len(top_rules)] == top_rules
    )
    assert len(both["findings"]) >= len(rules_only["findings"])
    assert detector.explain(frame.iloc[0].to_dict(), peer_rate=1400)
    assert detector.lineage().method == "iforest_ecod"
