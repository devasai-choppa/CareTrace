"""Fellegi-Sunter entity resolution with an EM estimate of m and u.

Splink implements this same model. CareTrace fits it directly so training does
not depend on a second DuckDB build, and records the learned parameters on the
lineage of every linked pair. A Splink backend is used when the package imports
and CARETRACE_ER_BACKEND=splink.
"""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from rapidfuzz.distance import JaroWinkler

from caretrace.models.lineage import Lineage, now_utc
from caretrace.parse.payer_resolve import normalize_payer

COMPARISONS = ("name_high", "name_mid", "token_overlap", "plan_overlap", "state_compatible")
STATE_CANON = {
    "al": "al",
    "ak": "ak",
    "az": "az",
    "ar": "ar",
    "ca": "ca",
    "california": "ca",
    "co": "co",
    "ct": "ct",
    "dc": "dc",
    "de": "de",
    "fl": "fl",
    "florida": "fl",
    "ga": "ga",
    "hi": "hi",
    "ia": "ia",
    "id": "id",
    "il": "il",
    "in": "in",
    "ks": "ks",
    "ky": "ky",
    "la": "la",
    "ma": "ma",
    "massachusetts": "ma",
    "md": "md",
    "me": "me",
    "mi": "mi",
    "mn": "mn",
    "mo": "mo",
    "ms": "ms",
    "mt": "mt",
    "nc": "nc",
    "nd": "nd",
    "ne": "ne",
    "nh": "nh",
    "nj": "nj",
    "nm": "nm",
    "nv": "nv",
    "ny": "ny",
    "newyork": "ny",
    "oh": "oh",
    "ok": "ok",
    "or": "or",
    "pa": "pa",
    "ri": "ri",
    "sc": "sc",
    "sd": "sd",
    "tn": "tn",
    "tx": "tx",
    "texas": "tx",
    "ut": "ut",
    "va": "va",
    "vt": "vt",
    "wa": "wa",
    "wi": "wi",
    "wv": "wv",
    "wy": "wy",
}


@dataclass
class Pair:
    """Two payer or provider strings and an optional hand label."""

    left_name: str
    right_name: str
    left_plan: str = ""
    right_plan: str = ""
    label: int | None = None


def _tokens(text: str) -> set[str]:
    return set(normalize_payer(text).split())


def _jaccard(left: str, right: str) -> float:
    a, b = _tokens(left), _tokens(right)
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _states(text: str) -> set[str]:
    return {STATE_CANON[token] for token in _tokens(text) if token in STATE_CANON}


def _state_compatible(left: str, right: str) -> int:
    found_left, found_right = _states(left), _states(right)
    if found_left and found_right and found_left.isdisjoint(found_right):
        return 0
    return 1


def agreement_vector(pair: Pair) -> list[int]:
    """Binary comparison vector. Each 1 is an agreement."""
    jw = JaroWinkler.normalized_similarity(
        normalize_payer(pair.left_name), normalize_payer(pair.right_name)
    )
    plan = _jaccard(pair.left_plan, pair.right_plan) if pair.left_plan and pair.right_plan else 0.0
    return [
        int(jw >= 0.92),
        int(jw >= 0.80),
        int(_jaccard(pair.left_name, pair.right_name) >= 0.5),
        int(plan >= 0.6),
        _state_compatible(pair.left_name, pair.right_name),
    ]


class FellegiSunterLinker:
    """Fellegi-Sunter matcher. explain() returns the match-weight waterfall."""

    method = "fellegi_sunter_em"

    def __init__(self) -> None:
        self.m = np.full(len(COMPARISONS), 0.9)
        self.u = np.full(len(COMPARISONS), 0.1)
        self.prior = 0.3
        self.parameters: dict[str, object] = {}

    def fit(self, pairs: list[Pair], iterations: int = 25) -> FellegiSunterLinker:
        """Estimate m, u, and the match prior with expectation maximization."""
        gammas = np.array([agreement_vector(pair) for pair in pairs], dtype=float)
        if len(gammas) == 0:
            raise ValueError("need at least one pair to fit")
        m = np.full(gammas.shape[1], 0.9)
        u = np.clip(gammas.mean(axis=0), 0.02, 0.4)
        prior = 0.3
        for _ in range(iterations):
            posterior = self._posterior(gammas, m, u, prior)
            prior = float(np.clip(posterior.mean(), 0.01, 0.99))
            match_mass = float(posterior.sum()) + 1e-9
            unmatch_mass = float((1 - posterior).sum()) + 1e-9
            m = np.clip((posterior[:, None] * gammas).sum(axis=0) / match_mass, 0.05, 0.99)
            u = np.clip(((1 - posterior)[:, None] * gammas).sum(axis=0) / unmatch_mass, 0.001, 0.9)
            u = np.minimum(u, m - 0.02)
        self.m, self.u, self.prior = m, u, prior
        self.parameters = {
            "m": [round(float(v), 4) for v in m],
            "u": [round(float(v), 4) for v in u],
            "prior": round(prior, 4),
            "comparisons": list(COMPARISONS),
            "iterations": iterations,
        }
        return self

    def match_probability(self, pair: Pair) -> float:
        """Posterior probability that the pair is a match.

        Blocking rule: when both sides name a plan and the plan tokens do not
        overlap, the pair is not a plan-level match. Name agreement alone cannot
        override that block.
        """
        if pair.left_plan and pair.right_plan and _jaccard(pair.left_plan, pair.right_plan) < 0.2:
            return 0.05
        gamma = np.array([agreement_vector(pair)], dtype=float)
        return float(self._posterior(gamma, self.m, self.u, self.prior)[0])

    def weights(self, pair: Pair) -> list[tuple[str, float]]:
        """Match weight (log2 of the likelihood ratio) for each comparison."""
        gamma = agreement_vector(pair)
        rows = []
        for name, agree, m_value, u_value in zip(COMPARISONS, gamma, self.m, self.u, strict=True):
            if agree:
                weight = math.log2(float(m_value) / float(u_value))
            else:
                weight = math.log2((1 - float(m_value)) / (1 - float(u_value)))
            rows.append((name, weight))
        return rows

    def explain(self, pair: Pair) -> list[str]:
        """Plain-language waterfall, largest absolute weight first."""
        probability = self.match_probability(pair)
        lines = [f"Match probability {probability:.2f} using {self.method}."]
        if pair.left_plan and pair.right_plan and _jaccard(pair.left_plan, pair.right_plan) < 0.2:
            lines.append("Blocked: plan names do not overlap, so this is not a plan-level match.")
        ordered = sorted(self.weights(pair), key=lambda item: abs(item[1]), reverse=True)
        for name, weight in ordered:
            direction = "for a match" if weight >= 0 else "against a match"
            lines.append(f"{name} contributes {weight:+.2f} bits {direction}.")
        return lines

    def save_waterfall(self, pair: Pair, path: Path) -> Path:
        """Save a horizontal bar chart of match weights."""
        weights = self.weights(pair)
        labels = [name for name, _ in weights]
        values = [weight for _, weight in weights]
        path.parent.mkdir(parents=True, exist_ok=True)
        _fig, axis = plt.subplots(figsize=(7, 3.5))
        colors = ["#0F766E" if value >= 0 else "#B45309" for value in values]
        axis.barh(labels, values, color=colors)
        axis.axvline(0, color="#1F2933", linewidth=0.8)
        axis.set_xlabel("Match weight (bits)")
        axis.set_title("Why these records linked")
        _fig.tight_layout()
        _fig.savefig(path, dpi=120)
        plt.close(_fig)
        return path

    def lineage(self, source_id: str = "er") -> Lineage:
        """Lineage record carrying the fitted method and parameters."""
        return Lineage(
            source_id=source_id,
            source_file_uri="model://fellegi-sunter",
            source_row_hash="fellegi-sunter",
            ingested_at=now_utc(),
            parser_version="er-1.0.0",
            method=self.method,
            parameters=self.parameters,
        )

    @staticmethod
    def _posterior(gammas: np.ndarray, m: np.ndarray, u: np.ndarray, prior: float) -> np.ndarray:
        log_m = gammas * np.log(m) + (1 - gammas) * np.log(1 - m)
        log_u = gammas * np.log(u) + (1 - gammas) * np.log(1 - u)
        log_match = math.log(prior) + log_m.sum(axis=1)
        log_unmatch = math.log(1 - prior) + log_u.sum(axis=1)
        shift = np.maximum(log_match, log_unmatch)
        match = np.exp(log_match - shift)
        unmatch = np.exp(log_unmatch - shift)
        return match / (match + unmatch)


def build_scaffold_pairs(n_each: int = 100) -> list[Pair]:
    """300 labeled pairs: true aliases, same-brand different-state, and random non-matches."""
    trues = [
        ("Blue Cross Blue Shield of Massachusetts", "Blue Cross Blue Shield of MA"),
        ("Harvard Pilgrim Health Care", "Harvard Pilgrim Health Plan"),
        ("Tufts Health Plan", "Tufts Health"),
    ]
    randoms = [
        ("Tufts Health Plan", "Aetna Better Health"),
        ("Harvard Pilgrim Health Care", "Cigna Medical Group"),
        ("Blue Cross Blue Shield of Massachusetts", "Kaiser Permanente"),
    ]
    pairs: list[Pair] = []
    for index in range(n_each):
        left, right = trues[index % len(trues)]
        # Same plan, minor alias. Both methods should match these.
        pairs.append(Pair(left, right, "HMO Blue", "HMO Blue", 1))
        # Same payer string, different plan. Name-only fuzzy matches; plan evidence does not.
        pairs.append(Pair(f"{left} {index}", f"{left} {index}", "HMO Blue", "Medicaid ACO", 0))
        left_r, right_r = randoms[index % len(randoms)]
        pairs.append(Pair(left_r, f"{right_r} {index}", "PPO", "HMO", 0))
    return pairs


def write_scaffold(path: Path, pairs: list[Pair] | None = None) -> Path:
    """Write the hand-label scaffold. Labels are the gold match flag."""
    rows = pairs or build_scaffold_pairs()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["left_name", "right_name", "left_plan", "right_plan", "label"],
        )
        writer.writeheader()
        for pair in rows:
            writer.writerow(
                {
                    "left_name": pair.left_name,
                    "right_name": pair.right_name,
                    "left_plan": pair.left_plan,
                    "right_plan": pair.right_plan,
                    "label": pair.label,
                }
            )
    return path


def read_scaffold(path: Path) -> list[Pair]:
    """Load labeled pairs."""
    with path.open(encoding="utf-8") as handle:
        return [
            Pair(
                row["left_name"],
                row["right_name"],
                row.get("left_plan", ""),
                row.get("right_plan", ""),
                int(row["label"]),
            )
            for row in csv.DictReader(handle)
        ]


def binary_f1(y_true: list[int], y_pred: list[int]) -> float:
    """F1 for the positive (match) class."""
    tp = sum(1 for truth, pred in zip(y_true, y_pred, strict=True) if truth == 1 and pred == 1)
    fp = sum(1 for truth, pred in zip(y_true, y_pred, strict=True) if truth == 0 and pred == 1)
    fn = sum(1 for truth, pred in zip(y_true, y_pred, strict=True) if truth == 1 and pred == 0)
    if tp == 0:
        return 0.0
    precision = tp / (tp + fp)
    recall = tp / (tp + fn)
    return 2 * precision * recall / (precision + recall)


def baseline_predict(pair: Pair, threshold: float = 90) -> int:
    """Rapidfuzz token-set baseline used by the P06 resolver."""
    from rapidfuzz import fuzz

    score = fuzz.token_set_ratio(normalize_payer(pair.left_name), normalize_payer(pair.right_name))
    return int(score >= threshold)
