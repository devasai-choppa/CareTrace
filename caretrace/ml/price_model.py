"""Conformalized quantile regression for missing dollar rates.

Published dollar rates are never replaced. Imputed intervals are labeled imputed
and are not a hospital's published price.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.preprocessing import OrdinalEncoder

from caretrace.models.lineage import Lineage, now_utc

FEATURES = [
    "code_family",
    "setting",
    "hospital",
    "payer",
    "plan_type",
    "gross_charge",
    "discounted_cash",
    "peer_median",
    "transparency_score",
]
NUMERIC = ["gross_charge", "discounted_cash", "peer_median", "transparency_score"]
CATEGORICAL = ["code_family", "setting", "hospital", "payer", "plan_type"]


class PriceModel:
    """Light-weight quantile gradient boosting with split conformal intervals.

    The production swap is LightGBM plus MAPIE. This class is the same
    conformalized quantile regression procedure: three quantile models and a
    calibration residual, with a separate threshold per code family (Mondrian).
    """

    method = "conformalized_quantile_regression"

    def __init__(self, coverage: float = 0.9, random_state: int = 0) -> None:
        self.coverage = coverage
        self.random_state = random_state
        self.encoder = OrdinalEncoder(handle_unknown="use_encoded_value", unknown_value=-1)
        self.models: dict[float, GradientBoostingRegressor] = {}
        self.global_q: float = 0.0
        self.group_q: dict[str, float] = {}
        self.background: dict[str, float] = {}
        self._train_frame: pd.DataFrame | None = None
        self.parameters: dict[str, Any] = {"coverage": coverage, "quantiles": [0.1, 0.5, 0.9]}

    def fit(self, frame: pd.DataFrame, calibration_fraction: float = 0.3) -> PriceModel:
        """Fit q=0.1, 0.5, 0.9 on log price and calibrate 90% intervals."""
        data = frame.reset_index(drop=True)
        if len(data) < 30:
            raise ValueError("need at least 30 rows to fit the price model")
        cutoff = max(10, int(len(data) * (1 - calibration_fraction)))
        train = data.iloc[:cutoff]
        calib = data.iloc[cutoff:]
        self.encoder.fit(train[CATEGORICAL].astype(str))
        y_train = np.log(train["price"].astype(float).clip(lower=1))
        x_train = self._matrix(train)
        for alpha in (0.1, 0.5, 0.9):
            model = GradientBoostingRegressor(
                loss="quantile",
                alpha=alpha,
                n_estimators=40,
                max_depth=3,
                random_state=self.random_state,
            )
            model.fit(x_train, y_train)
            self.models[alpha] = model
        scores = self._scores(calib)
        self.global_q = _conformal_quantile(scores, self.coverage)
        self.group_q = {}
        families = calib["code_family"].astype(str).to_numpy()
        for family in sorted(set(families)):
            mask = families == family
            if int(mask.sum()) >= 8:
                self.group_q[family] = _conformal_quantile(scores[mask], self.coverage)
        self.background = {column: float(train[column].median()) for column in NUMERIC}
        self._train_frame = train
        self.parameters = {
            "coverage": self.coverage,
            "quantiles": [0.1, 0.5, 0.9],
            "n_train": int(len(train)),
            "n_calibration": int(len(calib)),
            "global_q": round(float(self.global_q), 4),
            "n_mondrian_groups": len(self.group_q),
        }
        return self

    def predict_row(self, row: dict[str, Any]) -> dict[str, Any]:
        """Point estimate and interval on the dollar scale. Always marked imputed."""
        frame = pd.DataFrame([{column: row.get(column) for column in FEATURES}])
        low, mid, high = self._quantiles(frame)
        family = str(row.get("code_family"))
        slack = self.group_q.get(family, self.global_q)
        low_price = float(np.exp(low[0] - slack))
        high_price = float(np.exp(high[0] + slack))
        point = float(np.exp(mid[0]))
        return {
            "point": point,
            "low": min(low_price, point),
            "high": max(high_price, point),
            "imputed": True,
            "method": self.method,
        }

    def explain(self, row: dict[str, Any]) -> list[str]:
        """Local permutation contributions, in plain words, largest first."""
        base = self.predict_row(row)["point"]
        impacts: list[tuple[str, float]] = []
        for column in NUMERIC:
            altered = dict(row)
            altered[column] = self.background.get(column, row.get(column))
            delta = base - self.predict_row(altered)["point"]
            impacts.append((column, delta))
        impacts.sort(key=lambda item: abs(item[1]), reverse=True)
        lines = []
        peer = float(row.get("peer_median") or 0) or 1
        gross = float(row.get("gross_charge") or 0)
        for column, delta in impacts[:5]:
            direction = "higher" if delta > 0 else "lower"
            if column == "gross_charge" and peer:
                lines.append(
                    f"{direction.capitalize()} because this hospital's gross charge is {gross / peer:.1f}x the peer median (${delta:+.0f})."
                )
            else:
                lines.append(
                    f"{direction.capitalize()} because {column.replace('_', ' ')} shifts the estimate by ${delta:+.0f}."
                )
        return lines

    def empirical_coverage(self, frame: pd.DataFrame) -> dict[str, float]:
        """Fraction of rows whose published price falls inside the interval."""
        covered = []
        by_group: dict[str, list[bool]] = {}
        widths = []
        errors = []
        for record in frame.to_dict(orient="records"):
            pred = self.predict_row(record)
            price = float(record["price"])
            hit = pred["low"] <= price <= pred["high"]
            covered.append(hit)
            by_group.setdefault(str(record["code_family"]), []).append(hit)
            widths.append(pred["high"] - pred["low"])
            errors.append(abs(pred["point"] - price))
        report = {
            "overall": float(np.mean(covered)) if covered else 0.0,
            "mae": float(np.mean(errors)),
        }
        report["mean_width"] = float(np.mean(widths)) if widths else 0.0
        for family, hits in by_group.items():
            report[f"family:{family}"] = float(np.mean(hits))
        return report

    def lineage(self) -> Lineage:
        """Lineage carrying the conformal method and fitted parameters."""
        return Lineage(
            source_id="price-model",
            source_file_uri="model://cqr",
            source_row_hash="cqr",
            ingested_at=now_utc(),
            parser_version="ml-1.0.0",
            method=self.method,
            parameters=self.parameters,
        )

    def _matrix(self, frame: pd.DataFrame) -> np.ndarray:
        cats = self.encoder.transform(frame[CATEGORICAL].astype(str))
        nums = frame[NUMERIC].astype(float).to_numpy()
        return np.hstack([cats, nums])

    def _quantiles(self, frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        matrix = self._matrix(frame)
        return (
            self.models[0.1].predict(matrix),
            self.models[0.5].predict(matrix),
            self.models[0.9].predict(matrix),
        )

    def _scores(self, frame: pd.DataFrame) -> np.ndarray:
        low, _mid, high = self._quantiles(frame)
        y = np.log(frame["price"].astype(float).clip(lower=1).to_numpy())
        return np.maximum(low - y, y - high)


def _conformal_quantile(scores: np.ndarray, coverage: float) -> float:
    """Finite-sample conformal quantile. Uses the max score when the level is 1."""
    n = len(scores)
    if n == 0:
        return 0.0
    level = min(1.0, math_ceil_level(n, coverage))
    return float(np.quantile(scores, level, method="higher"))


def math_ceil_level(n: int, coverage: float) -> float:
    return float(np.ceil((n + 1) * coverage) / n)


def synthetic_training_frame(n: int = 1500, seed: int = 0) -> pd.DataFrame:
    """Known price process used to check coverage. Not hospital data."""
    rng = np.random.default_rng(seed)
    families = np.array(["70", "80", "99", "27"])
    settings = np.array(["outpatient", "inpatient"])
    hospitals = np.array(["mgh", "bwh", "bidmc", "tufts", "bmc"])
    payers = np.array(["bcbsma", "harvard-pilgrim", "tufts-health"])
    family = rng.choice(families, n)
    setting = rng.choice(settings, n)
    hospital = rng.choice(hospitals, n)
    payer = rng.choice(payers, n)
    gross = rng.uniform(200, 5000, n)
    cash = gross * rng.uniform(0.2, 0.5, n)
    peer = rng.uniform(200, 3000, n)
    transparency = rng.uniform(0.6, 1.0, n)
    family_effect = {"70": 0.4, "80": 0.1, "99": -0.2, "27": 0.8}
    hospital_effect = {"mgh": 0.15, "bwh": 0.05, "bidmc": 0.1, "tufts": 0.0, "bmc": -0.1}
    log_price = (
        np.log(peer)
        + np.array([family_effect[item] for item in family])
        + np.array([hospital_effect[item] for item in hospital])
        + 0.15 * np.log(gross / peer)
        + rng.normal(0, 0.08, n)
    )
    return pd.DataFrame(
        {
            "code_family": family,
            "setting": setting,
            "hospital": hospital,
            "payer": payer,
            "plan_type": np.where(rng.random(n) > 0.5, "hmo", "ppo"),
            "gross_charge": gross,
            "discounted_cash": cash,
            "peer_median": peer,
            "transparency_score": transparency,
            "price": np.exp(log_price),
        }
    )
