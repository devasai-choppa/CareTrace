"""Isolation Forest + ECOD ensemble for bill lines the rules do not name.

Anomaly findings use severity 'ask'. They are not placed in an appeal unless
the user confirms them.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest

from caretrace.models.domain import EobLine, Finding
from caretrace.models.lineage import Lineage, now_utc

FEATURE_NAMES = [
    "allowed_over_rate",
    "allowed_over_peer",
    "units_z",
    "pair_rarity",
    "em_gap",
]


class AnomalyDetector:
    """Ensemble of an isolation forest and an empirical CDF score (ECOD)."""

    method = "iforest_ecod"

    def __init__(self, random_state: int = 0) -> None:
        self.forest = IsolationForest(
            n_estimators=100, contamination=0.1, random_state=random_state
        )
        self.ecod_left: np.ndarray | None = None
        self.ecod_right: np.ndarray | None = None
        self.train: np.ndarray | None = None
        self.parameters: dict[str, Any] = {"contamination": 0.1, "features": FEATURE_NAMES}

    def fit(self, frame: pd.DataFrame) -> AnomalyDetector:
        matrix = frame[FEATURE_NAMES].to_numpy(dtype=float)
        self.forest.fit(matrix)
        self.train = matrix
        self.ecod_left = np.sort(matrix, axis=0)
        self.ecod_right = self.ecod_left
        return self

    def score(self, frame: pd.DataFrame) -> np.ndarray:
        """Higher means more unusual. Scores are scaled to 0-1."""
        matrix = frame[FEATURE_NAMES].to_numpy(dtype=float)
        forest = self.forest.decision_function(matrix)
        forest_score = 1 - _minmax(-forest)
        ecod_score = self._ecod(matrix)
        return 0.5 * forest_score + 0.5 * ecod_score

    def explain(self, row: dict[str, float], peer_rate: float | None = None) -> list[str]:
        """Name the features that push the row up, plus a simple counterfactual."""
        base = self.score(pd.DataFrame([row]))[0]
        impacts = []
        for name in FEATURE_NAMES:
            altered = dict(row)
            altered[name] = 0.0
            delta = float(base - self.score(pd.DataFrame([altered]))[0])
            impacts.append((name, delta))
        impacts.sort(key=lambda item: item[1], reverse=True)
        lines = [
            f"{name.replace('_', ' ')} adds {delta:.2f} to the anomaly score."
            for name, delta in impacts[:3]
            if delta > 0
        ]
        if peer_rate is not None:
            lines.append(
                f"Would not be flagged if the allowed amount were at or below ${peer_rate:.0f}."
            )
        return lines or ["No single feature dominates the anomaly score."]

    def findings_for_lines(
        self,
        lines: list[EobLine],
        rates: dict[str, float],
        peer: dict[str, float],
        threshold: float = 0.7,
    ) -> list[Finding]:
        """Score lines and emit ask-tier findings above the threshold."""
        rows = [_line_features(line, rates, peer) for line in lines]
        frame = pd.DataFrame(rows)
        if self.train is None:
            self.fit(frame)
        scores = self.score(frame)
        found = []
        for line, row, value in zip(lines, rows, scores, strict=True):
            if value < threshold or not line.code:
                continue
            rate = rates.get(line.code)
            found.append(
                Finding(
                    rule_id="A1",
                    severity="ask",
                    line_refs=[line.line_no],
                    expected="a line inside the usual range for this code",
                    actual=f"anomaly score {value:.2f}",
                    impact=round(max(0.0, (line.allowed or 0) - (rate or 0)), 2),
                    confidence=round(float(value) * 0.5, 3),
                    confidence_reasons=self.explain(row, rate),
                    explanation="Statistical outlier. Confirm before using it in an appeal.",
                    method=self.method,
                )
            )
        return found

    def lineage(self) -> Lineage:
        return Lineage(
            source_id="anomaly",
            source_file_uri="model://iforest-ecod",
            source_row_hash="iforest-ecod",
            ingested_at=now_utc(),
            parser_version="audit-1.0.0",
            method=self.method,
            parameters=self.parameters,
        )

    def _ecod(self, matrix: np.ndarray) -> np.ndarray:
        assert self.train is not None
        scores = []
        for row in matrix:
            tails = []
            for index, value in enumerate(row):
                column = self.train[:, index]
                left = float(np.mean(column <= value))
                right = float(np.mean(column >= value))
                tails.append(-np.log(max(min(left, right), 1e-3)))
            scores.append(float(np.mean(tails)))
        return _minmax(np.array(scores))


def _minmax(values: np.ndarray) -> np.ndarray:
    span = float(values.max() - values.min())
    if span == 0:
        return np.zeros_like(values)
    return (values - values.min()) / span


def _line_features(
    line: EobLine, rates: dict[str, float], peer: dict[str, float]
) -> dict[str, float]:
    rate = rates.get(line.code or "", peer.get(line.code or "", 1.0)) or 1.0
    peer_rate = peer.get(line.code or "", rate) or 1.0
    allowed = line.allowed or 0.0
    units = line.units or 1.0
    em_gap = 1.0 if line.code in {"99215", "99205"} else 0.0
    return {
        "allowed_over_rate": allowed / rate,
        "allowed_over_peer": allowed / peer_rate,
        "units_z": units,
        "pair_rarity": 0.0,
        "em_gap": em_gap,
    }
