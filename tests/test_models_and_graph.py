from __future__ import annotations

from pathlib import Path

import numpy as np
from caretrace.directory.payer_directory import MockDirectory
from caretrace.directory.trust import normalize_address, score_entry
from caretrace.directory.trust_model import (
    AdditiveTrustModel,
    evaluate_trust,
    inject_ghosts,
    reliability_diagram,
    weak_label,
)
from caretrace.er.linkage import (
    FellegiSunterLinker,
    baseline_predict,
    binary_f1,
    build_scaffold_pairs,
)
from caretrace.graph.build_graph import build_graph
from caretrace.graph.queries import cheapest_in_network
from caretrace.ml.price_model import PriceModel, synthetic_training_frame
from caretrace.pipeline import load_samples


def test_fellegi_sunter_beats_baseline(tmp_path: Path) -> None:
    pairs = build_scaffold_pairs(100)
    assert len(pairs) == 300
    model = FellegiSunterLinker().fit(pairs)
    labels = [int(pair.label) for pair in pairs]
    probabilistic = [int(model.match_probability(pair) >= 0.5) for pair in pairs]
    baseline = [baseline_predict(pair) for pair in pairs]
    assert binary_f1(labels, probabilistic) > binary_f1(labels, baseline)
    explained = model.explain(pairs[0])
    assert "Match probability" in explained[0]
    chart = model.save_waterfall(pairs[0], tmp_path / "waterfall.png")
    assert chart.exists()
    assert model.lineage().method == "fellegi_sunter_em"


def test_graph_cheapest(settings) -> None:
    load_samples(settings)
    build_graph(settings)
    result = cheapest_in_network("73721", "bcbsma", "Boston", settings=settings)
    assert result["rows"]
    assert result["rows"][0]["rate"] == min(row["rate"] for row in result["rows"])
    assert result["evidence"][0]["source_row_hash"]
    assert "MATCH" in result["cypher"]


def test_conformal_coverage() -> None:
    frame = synthetic_training_frame(1600, seed=0)
    model = PriceModel().fit(frame.iloc[:1100])
    report = model.empirical_coverage(frame.iloc[1100:])
    assert 0.85 <= report["overall"] <= 0.97
    for key, value in report.items():
        if key.startswith("family:"):
            assert 0.80 <= value <= 1.0
    row = frame.iloc[0].to_dict()
    text = model.explain(row)
    assert text
    assert model.predict_row(row)["imputed"] is True
    assert model.lineage().parameters["coverage"] == 0.9


def test_trust_signals_and_model(tmp_path: Path) -> None:
    assert "st" in normalize_address("55 Fruit Street")
    directory = MockDirectory(path=Path("data/samples/directory/bcbsma_providers.json"))
    real = score_entry(directory.search("bcbsma", npi="1234567893")[0])
    ghost = score_entry(directory.search("bcbsma", npi="1000000001")[0])
    assert real.score > ghost.score
    assert ghost.ghost_risk
    assert real.reasons
    frame, labels = inject_ghosts(400, seed=1)
    labeled = np.array([weak_label(row) for row in frame.to_dict(orient="records")], dtype=float)
    # Fall back to the known ghost label when a row abstains.
    train_y = np.where(np.isnan(labeled), labels, labeled)
    model = AdditiveTrustModel().fit(frame, train_y)
    metrics = evaluate_trust(frame, labels, model)
    assert metrics["model_auc"] > metrics["baseline_auc"]
    path = reliability_diagram(model.predict_proba(frame), labels, tmp_path / "reliability.png")
    assert path.exists()
    assert model.explain(frame.iloc[0].to_dict())
