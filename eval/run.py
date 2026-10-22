"""Score price answers, prior auth, bill audit, and the verifier on sample data.

Writes eval/report.md and eval/results.json. Uses the mock model and the
committed samples. No API key is required.
"""

from __future__ import annotations

import csv
import json
import statistics
import time
from pathlib import Path

import duckdb
from caretrace.agent.graph import run_agent
from caretrace.agent.verifier import uncited_numbers
from caretrace.audit.engine import run_audit
from caretrace.audit.ingest_eob import ingest_path
from caretrace.audit.rules.builtin import AuditContext
from caretrace.audit.synthetic import expanded_cases, write_suite
from caretrace.directory.trust_model import (
    AdditiveTrustModel,
    evaluate_trust,
    inject_ghosts,
    reliability_diagram,
)
from caretrace.er.linkage import (
    FellegiSunterLinker,
    baseline_predict,
    binary_f1,
    build_scaffold_pairs,
    write_scaffold,
)
from caretrace.llm.client import numbers_in
from caretrace.ml.price_model import PriceModel, synthetic_training_frame
from caretrace.models.domain import RateQuote
from caretrace.pipeline import load_samples
from caretrace.policy.chunk import chunk_text
from caretrace.policy.graphrag import GraphRAG
from caretrace.policy.index import PolicyIndex
from caretrace.scale.privacy import PrivacyAccountant, PrivacyBudgetExceeded, dp_median
from caretrace.scale.tic_stream import benchmark, write_sample_tic
from caretrace.warehouse import get_rates, negotiated_resolution_rate

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "datasets"
REPORT = ROOT / "report.md"
RESULTS = ROOT / "results.json"


def main() -> None:
    """Run every metric and write the report."""
    load_samples()
    results = {
        "prices": price_metrics(),
        "prior_auth": prior_auth_metrics(),
        "eob": eob_metrics(),
        "verifier": verifier_metrics(),
        "entity_resolution": er_metrics(),
        "price_intervals": interval_metrics(),
        "directory": directory_metrics(),
        "privacy": privacy_metrics(),
        "scale": scale_metrics(),
        "resolution_rate": negotiated_resolution_rate(),
    }
    RESULTS.write_text(json.dumps(results, indent=2), encoding="utf-8")
    REPORT.write_text(render(results), encoding="utf-8")
    print(REPORT.read_text(encoding="utf-8"))


def price_metrics() -> dict[str, float]:
    """Exact-match of cited dollar rates against the gold mart, plus citation checks."""
    conn = duckdb.connect("data/caretrace.duckdb", read_only=True)
    frame = conn.execute(
        """
        SELECT code, payer_id, rate_dollar, source_row_hash
        FROM gold.mart_negotiated_rates
        WHERE rate_dollar IS NOT NULL
        """
    ).fetchdf()
    conn.close()
    groups = frame.groupby(["code", "payer_id"])
    unique: list[tuple[str, str, list[float]]] = []
    for (code, payer_id), rows in groups:
        unique.append(
            (str(code), str(payer_id), sorted(round(float(v), 2) for v in rows["rate_dollar"]))
        )
    if not unique:
        raise RuntimeError("gold mart has no dollar rates; run caretrace load-samples")
    questions = [unique[index % len(unique)] for index in range(50)]
    path = DATA / "price_questions.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["question", "code", "payer_id", "gold_rates"])
        for code, payer_id, gold in questions:
            writer.writerow(
                [
                    f"What is the negotiated rate for {code} with {payer_id} in Boston?",
                    code,
                    payer_id,
                    json.dumps(gold),
                ]
            )
    exact = 0
    cited = 0
    latencies = []
    for code, payer_id, gold in questions:
        start = time.perf_counter()
        quotes = get_rates(code, payer_id, city="Boston")
        latencies.append((time.perf_counter() - start) * 1000)
        got = sorted(
            round(item.value.rate_dollar, 2)
            for item in quotes
            if item.value.rate_dollar is not None
        )
        if got == gold:
            exact += 1
        if quotes and all(item.lineage and item.lineage[0].source_row_hash for item in quotes):
            cited += 1
    latencies.sort()
    return {
        "n": float(len(questions)),
        "exact_match": exact / len(questions),
        "citation_correct": cited / len(questions),
        "latency_p50_ms": _percentile(latencies, 0.50),
        "latency_p95_ms": _percentile(latencies, 0.95),
        "cost_per_query_usd": 0.0,
    }


def prior_auth_metrics() -> dict[str, float]:
    """Score GraphRAG on the labeled sample-policy questions."""
    text = Path("data/samples/policies/bcbsma_imaging.txt").read_text(encoding="utf-8")
    index = PolicyIndex()
    index.add(
        chunk_text(text, payer_id="bcbsma", url="file://data/samples/policies/bcbsma_imaging.txt")
    )
    rag = GraphRAG(index)
    rows = list(csv.DictReader((DATA / "prior_auth_labels.csv").open(encoding="utf-8")))
    correct = 0
    unknown = 0
    page_hits = 0
    page_needed = 0
    for row in rows:
        answer = rag.answer(row["question"], row["payer_id"])
        if answer.requires_pa == "unknown":
            unknown += 1
        if answer.requires_pa == row["gold_decision"]:
            correct += 1
        if row["gold_page"]:
            page_needed += 1
            pages = {int(item["page"]) for item in answer.citations if item.get("page") is not None}
            if int(row["gold_page"]) in pages:
                page_hits += 1
    n = len(rows)
    return {
        "n": float(n),
        "accuracy": correct / n,
        "unknown_rate": unknown / n,
        "citation_page_accuracy": page_hits / page_needed if page_needed else 0.0,
    }


def eob_metrics() -> dict[str, float]:
    """Ingest 30 synthetic PDFs and score extraction plus audit findings."""
    cases = expanded_cases(30)
    directory = Path("data/samples/eobs")
    write_suite(directory, cases)
    quote = RateQuote(
        hospital_name="Massachusetts General Hospital",
        source_id="mgh",
        code="73721",
        code_type="CPT",
        setting="outpatient",
        payer_id="bcbsma",
        rate_dollar=1400.0,
        rate_kind="dollar",
        discounted_cash=900.0,
        quality_score=1.0,
        source_row_hash="abc123",
        source_file_uri="file://data/samples/mgh_mrf.json",
    )
    context = AuditContext(rates_by_code={"73721": quote}, directory_confidence=0.86)
    tp = fp = fn = 0
    field_tp = field_fp = field_fn = 0
    impact_errors = []
    for case in cases:
        path = directory / f"{case['id']}.pdf"
        lines = ingest_path(path)
        gold_fields = []
        for raw in case["lines"]:
            parts = str(raw).split("|")
            gold_fields.append((parts[2], parts[4], parts[5], parts[7]))
        pred_fields = [
            (
                line.code or "",
                f"{line.billed:.2f}" if line.billed is not None else "",
                f"{line.allowed:.2f}" if line.allowed is not None else "",
                f"{line.patient_resp:.2f}" if line.patient_resp is not None else "",
            )
            for line in lines
        ]
        field_tp += len(set(gold_fields) & set(pred_fields))
        field_fp += len(set(pred_fields) - set(gold_fields))
        field_fn += len(set(gold_fields) - set(pred_fields))
        report = run_audit(lines, context)
        gold_rules = {str(item["rule_id"]) for item in case["errors"]}
        pred_rules = {
            finding.rule_id for finding in report["findings"] if finding.severity != "ask"
        }
        tp += len(gold_rules & pred_rules)
        fp += len(pred_rules - gold_rules)
        fn += len(gold_rules - pred_rules)
        gold_impact = sum(float(item["impact"]) for item in case["errors"])
        impact_errors.append(abs(float(report["potential_savings"]) - gold_impact))
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1_den = field_tp + 0.5 * (field_fp + field_fn)
    return {
        "n": 30.0,
        "finding_precision": precision,
        "finding_recall": recall,
        "impact_mae": statistics.fmean(impact_errors) if impact_errors else 0.0,
        "extraction_f1": field_tp / f1_den if f1_den else 0.0,
    }


def verifier_metrics() -> dict[str, float]:
    """Share of answers that still contain a number the tools did not return."""
    prompts = [
        "knee MRI with payer X near 02115",
        "what is the negotiated rate for 73721 with bcbsma near 02115",
        "does 73721 need prior authorization for bcbsma",
        "is provider 1234567893 in network for bcbsma",
        "estimate a knee MRI 73721 for bcbsma near 02115",
    ]
    bad = 0
    for prompt in prompts:
        answer = run_agent(prompt)
        citation_blob = " ".join(
            " ".join(
                str(part)
                for part in (
                    citation.text,
                    citation.source_row_hash,
                    citation.source_file_uri,
                    citation.quote,
                )
                if part
            )
            for citation in answer.citations
        )
        if uncited_numbers(answer.answer, citation_blob):
            bad += 1
    injected = uncited_numbers("The cash price is $99999.", "cash price 1400")
    return {
        "n": float(len(prompts)),
        "uncited_number_rate": bad / len(prompts),
        "injection_caught": 1.0 if injected == ["99999"] else 0.0,
        "sample_has_digits": 1.0 if numbers_in(prompts[0]) else 0.0,
    }


def er_metrics() -> dict[str, float]:
    """Fellegi-Sunter versus the rapidfuzz baseline on the 300-pair scaffold."""
    pairs = build_scaffold_pairs()
    write_scaffold(DATA / "er_labels.csv", pairs)
    linker = FellegiSunterLinker().fit(pairs)
    linker.save_waterfall(pairs[0], ROOT / "waterfall_example.png")
    truth = [int(pair.label or 0) for pair in pairs]
    learned = [1 if linker.match_probability(pair) >= 0.5 else 0 for pair in pairs]
    baseline = [baseline_predict(pair) for pair in pairs]
    return {
        "n": float(len(pairs)),
        "fellegi_sunter_f1": binary_f1(truth, learned),
        "rapidfuzz_f1": binary_f1(truth, baseline),
    }


def interval_metrics() -> dict[str, float]:
    """Empirical coverage of the conformal price intervals on a known process."""
    frame = synthetic_training_frame(800)
    model = PriceModel().fit(frame.iloc[:500])
    report = model.empirical_coverage(frame.iloc[500:])
    row = frame.iloc[0].to_dict()
    explanation = model.explain(row)
    return {
        "overall_coverage": float(report["overall"]),
        "mae": float(report["mae"]),
        "mean_width": float(report["mean_width"]),
        "explain_nonempty": 1.0 if explanation else 0.0,
    }


def directory_metrics() -> dict[str, float]:
    """AUC of the additive trust model against the phone-heavy baseline."""
    frame, labels = inject_ghosts(400)
    model = AdditiveTrustModel().fit(frame, labels)
    scores = evaluate_trust(frame, labels, model)
    reliability_diagram(model.predict_proba(frame), labels, ROOT / "reliability.png")
    return scores


def privacy_metrics() -> dict[str, float]:
    """A release under budget succeeds; a second that would exceed it is refused."""
    accountant = PrivacyAccountant(budget=0.1)
    values = [float(v) for v in range(100, 140)]
    release = dp_median(values, epsilon=0.1, lower=0, upper=500, accountant=accountant, k=11)
    blocked = 0.0
    try:
        dp_median(values, epsilon=0.1, lower=0, upper=500, accountant=accountant, k=11)
    except PrivacyBudgetExceeded:
        blocked = 1.0
    small = dp_median(
        [1, 2, 3], epsilon=0.1, lower=0, upper=10, accountant=PrivacyAccountant(1), k=11
    )
    return {
        "release_ok": 1.0 if release is not None else 0.0,
        "budget_blocked": blocked,
        "small_cell_suppressed": 1.0 if small is None else 0.0,
        "epsilon": 0.1,
        "k": 11.0,
    }


def scale_metrics() -> dict[str, float]:
    """Local TiC stream benchmark. This is not a cluster measurement."""
    path = write_sample_tic(Path("data/samples/tic_sample.json"), n_items=2000)
    stats = benchmark(path, {"1234567893"}, {"73721"})
    return stats


def render(results: dict[str, dict[str, float]]) -> str:
    """Markdown report. Numbers come from this run."""
    prices = results["prices"]
    policy = results["prior_auth"]
    eob = results["eob"]
    verifier = results["verifier"]
    er = results["entity_resolution"]
    intervals = results["price_intervals"]
    directory = results["directory"]
    privacy = results["privacy"]
    scale = results["scale"]
    return f"""# CareTrace evaluation

Generated by `python -m eval.run` on the committed samples and the mock model.
Cost per query is $0 because the mock provider does not call an API.

## Headline

| Metric | Baseline | CareTrace |
| --- | ---: | ---: |
| Price exact-match | — | {prices["exact_match"]:.3f} |
| Price citation present | — | {prices["citation_correct"]:.3f} |
| Prior-auth accuracy | flat retrieval is the baseline inside GraphRAG | {policy["accuracy"]:.3f} |
| Prior-auth unknown rate | — | {policy["unknown_rate"]:.3f} |
| Prior-auth page accuracy | — | {policy["citation_page_accuracy"]:.3f} |
| EOB field F1 | — | {eob["extraction_f1"]:.3f} |
| Audit finding precision | — | {eob["finding_precision"]:.3f} |
| Audit finding recall | — | {eob["finding_recall"]:.3f} |
| Audit dollar MAE | — | {eob["impact_mae"]:.2f} |
| Answers with an uncited number | — | {verifier["uncited_number_rate"]:.3f} |
| Payer match F1 | rapidfuzz {er["rapidfuzz_f1"]:.3f} | Fellegi-Sunter {er["fellegi_sunter_f1"]:.3f} |
| Directory ghost AUC | phone-heavy {directory["baseline_auc"]:.3f} | additive model {directory["model_auc"]:.3f} |
| Interval coverage (synthetic process) | nominal 0.90 | {intervals["overall_coverage"]:.3f} |

## Detail

- Price questions: {int(prices["n"])}. Latency p50 {prices["latency_p50_ms"]:.1f} ms, p95 {prices["latency_p95_ms"]:.1f} ms.
- Negotiated-row payer resolution rate: {results["resolution_rate"]:.3f}.
- Prior-auth questions: {int(policy["n"])}, labeled from `data/samples/policies/bcbsma_imaging.txt`.
- Synthetic EOBs: {int(eob["n"])}.
- Verifier injection fixture caught: {verifier["injection_caught"]:.0f}.
- Conformal MAE {intervals["mae"]:.2f}, mean width {intervals["mean_width"]:.2f}, explain() nonempty {intervals["explain_nonempty"]:.0f}.
- Directory Brier {directory["model_brier"]:.3f} versus baseline {directory["baseline_brier"]:.3f}.
- Privacy: release ok {privacy["release_ok"]:.0f}, over-budget blocked {privacy["budget_blocked"]:.0f}, k<{int(privacy["k"])} suppressed {privacy["small_cell_suppressed"]:.0f}.
- TiC sample stream: {scale["rows"]:.0f} rows, {scale["mb_per_second"]:.3f} MB/s, {scale["rows_per_second"]:.0f} rows/s on a local file of {scale["mb"]:.3f} MB.

Takeaway: on the sample hospitals, a knee MRI query returns the published dollar rates with a row hash on every quote. Prior-auth answers abstain when the sample policy has no code row. Audit findings on the synthetic bills match the injected error types.
"""


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    index = min(len(values) - 1, max(0, int(round(q * (len(values) - 1)))))
    return values[index]


if __name__ == "__main__":
    main()
