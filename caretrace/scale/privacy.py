"""Differentially private medians for employer market views.

Values are clipped, cells smaller than k are suppressed, and the release spends
epsilon from a budget. The mechanism is a Laplace-noised histogram median:
one person changes one bin by 1, so the L1 sensitivity of the histogram is 1.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from caretrace.models.lineage import Lineage, now_utc


class PrivacyBudgetExceeded(RuntimeError):
    """Raised when a release would spend more epsilon than the budget allows."""


@dataclass
class PrivacyAccountant:
    """Tracks epsilon spent on successive releases."""

    budget: float
    spent: float = 0.0
    releases: list[dict[str, float]] = field(default_factory=list)

    def spend(self, epsilon: float) -> None:
        if epsilon <= 0:
            raise ValueError("epsilon must be positive")
        if self.spent + epsilon > self.budget + 1e-9:
            raise PrivacyBudgetExceeded(
                f"release needs epsilon {epsilon}, budget left {self.budget - self.spent:.4f}"
            )
        self.spent += epsilon
        self.releases.append({"epsilon": epsilon, "spent": self.spent})

    def explain(self) -> list[str]:
        return [
            f"Privacy budget is {self.budget:.3f}.",
            f"Spent {self.spent:.3f} across {len(self.releases)} releases.",
        ]


def dp_median(
    values: list[float],
    *,
    epsilon: float,
    lower: float,
    upper: float,
    accountant: PrivacyAccountant,
    k: int = 11,
    bins: int = 20,
    seed: int = 0,
) -> dict[str, object] | None:
    """Release a noisy median, or None when the cell fails k-anonymity."""
    if len(values) < k:
        return None
    accountant.spend(epsilon)
    clipped = np.clip(np.array(values, dtype=float), lower, upper)
    counts, edges = np.histogram(clipped, bins=bins, range=(lower, upper))
    scale = 1.0 / epsilon
    rng = np.random.default_rng(seed)
    noisy = counts.astype(float) + rng.laplace(0.0, scale, size=len(counts))
    noisy = np.maximum(noisy, 0)
    total = noisy.sum()
    if total <= 0:
        median = float(np.median(clipped))
    else:
        cumulative = np.cumsum(noisy) / total
        index = int(np.searchsorted(cumulative, 0.5))
        index = min(index, len(edges) - 2)
        median = float((edges[index] + edges[index + 1]) / 2)
    return {
        "median": median,
        "n": len(values),
        "epsilon": epsilon,
        "noise_scale": scale,
        "k": k,
        "suppressed": False,
        "method": "laplace_histogram_median",
    }


def lineage_for(release: dict[str, object]) -> Lineage:
    """Lineage for a private release, including epsilon and noise scale."""
    return Lineage(
        source_id="dp-release",
        source_file_uri="privacy://median",
        source_row_hash="dp-median",
        ingested_at=now_utc(),
        parser_version="privacy-1.0.0",
        method=str(release["method"]),
        parameters={
            "epsilon": release["epsilon"],
            "noise_scale": release["noise_scale"],
            "k": release["k"],
        },
    )


def explain_release(release: dict[str, object] | None, accountant: PrivacyAccountant) -> list[str]:
    """Plain-language privacy explanation for the dashboard."""
    if release is None:
        return [
            "Cell suppressed because it has fewer than the k-anonymity threshold."
        ] + accountant.explain()
    return [
        f"Noisy median uses epsilon {release['epsilon']} and Laplace scale {float(str(release['noise_scale'])):.3f}.",
        f"Cell size {release['n']} meets k={release['k']}.",
        *accountant.explain(),
    ]
