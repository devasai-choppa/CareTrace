"""Glass-box trust model trained on weak labels from the directory signals.

The labeling functions follow the P08 rules. A backfitting additive model is the
glass-box stand-in for an Explainable Boosting Machine, calibrated with isotonic
regression. The heuristic score remains the baseline.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression
from sklearn.metrics import brier_score_loss, roc_auc_score

from caretrace.models.lineage import Lineage, now_utc

FEATURES = [
    "npi_active",
    "name_match",
    "address_match",
    "taxonomy_match",
    "phone_match",
    "recency",
    "mrf_corroboration",
    "stale_and_uncorroborated",
]


def labeling_functions(row: dict[str, float]) -> list[int]:
    """Return votes in {1 ghost, 0 real, -1 abstain}."""
    votes = []
    votes.append(1 if row["npi_active"] == 0 else -1)
    if row["mrf_corroboration"] == 0 and row.get("recency", 1) == 0:
        votes.append(1)
    elif row["mrf_corroboration"] == 1 and row["npi_active"] == 1 and row["address_match"] >= 0.8:
        votes.append(0)
    else:
        votes.append(-1)
    if row["name_match"] >= 0.9 and row["address_match"] >= 0.9 and row["npi_active"] == 1:
        votes.append(0)
    else:
        votes.append(-1)
    if row.get("stale_and_uncorroborated", 0) == 1:
        votes.append(1)
    else:
        votes.append(-1)
    return votes


def weak_label(row: dict[str, float]) -> float | None:
    """Probability a listing is a ghost, from non-abstaining labeling functions."""
    votes = [vote for vote in labeling_functions(row) if vote >= 0]
    if not votes:
        return None
    return float(sum(votes) / len(votes))


class AdditiveTrustModel:
    """One-dimensional binned effects, backfit, then isotonic calibration."""

    method = "additive_trust_ebm"

    def __init__(self, bins: int = 4) -> None:
        self.bins = bins
        self.effects: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        self.intercept = 0.0
        self.calibrator = IsotonicRegression(out_of_bounds="clip")
        self.parameters: dict[str, Any] = {"bins": bins}

    def fit(self, frame: pd.DataFrame, labels: np.ndarray) -> AdditiveTrustModel:
        """Fit additive log-odds and calibrate probabilities on the same design matrix."""
        matrix = frame[FEATURES].astype(float).to_numpy()
        y = labels.astype(float)
        residual = y - y.mean()
        self.intercept = float(y.mean())
        self.effects = {}
        for index, name in enumerate(FEATURES):
            column = matrix[:, index]
            centers, effect = _feature_effect(column, residual)
            self.effects[name] = (centers, effect)
            residual = residual - self._lookup(column, name)
        raw = self._raw_proba(frame)
        self.calibrator.fit(raw, y)
        self.parameters = {
            "bins": self.bins,
            "features": FEATURES,
            "intercept": round(self.intercept, 4),
        }
        return self

    def predict_proba(self, frame: pd.DataFrame) -> np.ndarray:
        """Calibrated probability that the listing is a ghost."""
        return self.calibrator.predict(self._raw_proba(frame))

    def explain(self, row: dict[str, float]) -> list[str]:
        """Per-feature contribution to the ghost score."""
        lines = []
        contributions = []
        for name in FEATURES:
            value = float(row[name])
            delta = float(self._lookup(np.array([value]), name)[0])
            contributions.append((name, delta, value))
        contributions.sort(key=lambda item: abs(item[1]), reverse=True)
        for name, delta, value in contributions:
            direction = "raises ghost risk" if delta > 0 else "lowers ghost risk"
            lines.append(f"{name.replace('_', ' ')} ({value:.2f}) {direction} by {delta:+.2f}.")
        return lines

    def lineage(self) -> Lineage:
        return Lineage(
            source_id="trust-model",
            source_file_uri="model://additive-trust",
            source_row_hash="additive-trust",
            ingested_at=now_utc(),
            parser_version="directory-1.0.0",
            method=self.method,
            parameters=self.parameters,
        )

    def _raw_proba(self, frame: pd.DataFrame) -> np.ndarray:
        total = np.full(len(frame), self.intercept)
        for name in FEATURES:
            total = total + self._lookup(frame[name].to_numpy(dtype=float), name)
        return np.clip(total, 0, 1)

    def _lookup(self, values: np.ndarray, name: str) -> np.ndarray:
        centers, effect = self.effects[name]
        index = np.abs(values.reshape(-1, 1) - centers.reshape(1, -1)).argmin(axis=1)
        return effect[index]


def _feature_effect(column: np.ndarray, residual: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Bin a feature. Low-cardinality columns get one bin per distinct value."""
    values = np.unique(column)
    if len(values) <= 6:
        centers = values.astype(float)
        effect = np.array(
            [
                float(residual[column == value].mean()) if np.any(column == value) else 0.0
                for value in values
            ]
        )
    else:
        edges = np.unique(np.quantile(column, np.linspace(0, 1, 5)))
        if len(edges) < 2:
            edges = np.array([float(column.min()) - 1e-6, float(column.max()) + 1e-6])
        centers_list: list[float] = []
        effect_list: list[float] = []
        for left, right in zip(edges[:-1], edges[1:], strict=True):
            mask = (column >= left) & (column <= right)
            centers_list.append((float(left) + float(right)) / 2)
            effect_list.append(float(residual[mask].mean()) if mask.any() else 0.0)
        centers = np.array(centers_list)
        effect = np.array(effect_list)
    effect = effect - effect.mean()
    return centers, effect


def heuristic_ghost_score(frame: pd.DataFrame) -> np.ndarray:
    """Hand weights that over-trust a matching phone number.

    That mistake is the baseline. Ghosts in the synthetic set keep the phone
    and drop the NPI, so a phone-heavy score ranks them as real.
    """
    weights = {
        "npi_active": 0.05,
        "name_match": 0.05,
        "address_match": 0.05,
        "taxonomy_match": 0.05,
        "phone_match": 0.70,
        "recency": 0.05,
        "mrf_corroboration": 0.05,
    }
    trust = np.zeros(len(frame))
    for name, weight in weights.items():
        trust = trust + frame[name].to_numpy(dtype=float) * weight
    return 1 - np.clip(trust, 0, 1)


def reliability_diagram(
    probabilities: np.ndarray, labels: np.ndarray, path: Path, bins: int = 8
) -> Path:
    """Save a reliability diagram. The diagonal is perfect calibration."""
    path.parent.mkdir(parents=True, exist_ok=True)
    edges = np.linspace(0, 1, bins + 1)
    xs, ys = [], []
    for left, right in zip(edges[:-1], edges[1:], strict=True):
        mask = (probabilities >= left) & (
            probabilities < right if right < 1 else probabilities <= right
        )
        if mask.sum() == 0:
            continue
        xs.append(float(probabilities[mask].mean()))
        ys.append(float(labels[mask].mean()))
    _fig, axis = plt.subplots(figsize=(4.5, 4))
    axis.plot([0, 1], [0, 1], linestyle="--", color="#94A3B8")
    axis.plot(xs, ys, marker="o", color="#0F766E")
    axis.set_xlabel("Predicted ghost probability")
    axis.set_ylabel("Observed frequency")
    axis.set_title("Directory trust reliability")
    _fig.tight_layout()
    _fig.savefig(path, dpi=120)
    plt.close(_fig)
    return path


def evaluate_trust(
    frame: pd.DataFrame, labels: np.ndarray, model: AdditiveTrustModel
) -> dict[str, float]:
    """AUC and Brier score for the learned model and the weighted baseline."""
    learned = model.predict_proba(frame)
    baseline = heuristic_ghost_score(frame)
    return {
        "model_auc": float(roc_auc_score(labels, learned)),
        "baseline_auc": float(roc_auc_score(labels, baseline)),
        "model_brier": float(brier_score_loss(labels, learned)),
        "baseline_brier": float(brier_score_loss(labels, np.clip(baseline, 0, 1))),
    }


def inject_ghosts(n: int = 500, seed: int = 0) -> tuple[pd.DataFrame, np.ndarray]:
    """Synthetic directory where phone match is a decoy and ghosts are deactivated or stale."""
    rng = np.random.default_rng(seed)
    rows = []
    labels = []
    for _ in range(n):
        ghost = bool(rng.random() < 0.45)
        if ghost:
            npi_active = 0.0
            recency = 0.0
            mrf = 0.0
            name = float(rng.random() < 0.4)
            address = float(rng.random() < 0.3)
            phone = 1.0
            taxonomy = float(rng.random() < 0.5)
        else:
            npi_active = 1.0
            recency = 1.0
            mrf = 1.0
            name = 1.0
            address = 1.0
            phone = 0.0
            taxonomy = 1.0
        stale_uncorroborated = 1.0 if recency == 0 and mrf == 0 else 0.0
        rows.append(
            {
                "npi_active": npi_active,
                "name_match": name,
                "address_match": address,
                "taxonomy_match": taxonomy,
                "phone_match": phone,
                "recency": recency,
                "mrf_corroboration": mrf,
                "stale_and_uncorroborated": stale_uncorroborated,
            }
        )
        labels.append(1 if ghost else 0)
    return pd.DataFrame(rows), np.array(labels)
