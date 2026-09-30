"""The ML incident classifier and its evaluation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.metrics import confusion_matrix, f1_score, precision_recall_fscore_support
from sklearn.neighbors import NearestNeighbors
from sklearn.preprocessing import RobustScaler

from .features import feature_names
from .rules import UNKNOWN

KNOWN = ["database_saturation", "deployment_regression", "traffic_spike", "dependency_failure"]
HELD_OUT = "memory_leak"
LABELS = [*KNOWN, HELD_OUT, UNKNOWN]


def make_estimator(kind: str, params: dict, seed: int = 0):
    if kind == "random_forest":
        return RandomForestClassifier(class_weight="balanced", random_state=seed, n_jobs=-1, **params)
    if kind == "gradient_boosting":
        return HistGradientBoostingClassifier(random_state=seed, **params)
    raise ValueError(kind)


@dataclass
class IncidentClassifier:
    kind: str
    params: dict
    unknown_threshold: float = 0.5
    features: list[str] = field(default_factory=feature_names)
    seed: int = 0
    novelty_threshold: float = float("inf")
    estimator: Any = None
    scaler: Any = None
    neighbours: Any = None

    def fit(self, x: pd.DataFrame, y: list[str]) -> IncidentClassifier:
        values = x[self.features].to_numpy()
        self.estimator = make_estimator(self.kind, self.params, self.seed)
        self.estimator.fit(values, np.asarray(y))
        # Open-set guard: remember what training windows look like, so windows far from all of
        # them can be routed to `unknown` even when the forest is confident.
        self.scaler = RobustScaler(quantile_range=(10, 90)).fit(values)
        self.neighbours = NearestNeighbors(n_neighbors=5).fit(self._scaled(values))
        return self

    def _scaled(self, values: np.ndarray) -> np.ndarray:
        return np.clip(self.scaler.transform(values), -50, 50)  # type: ignore[attr-defined]

    def novelty(self, x: pd.DataFrame) -> np.ndarray:
        """Mean distance to the 5 nearest training windows (robust-scaled feature space)."""
        dist, _ = self.neighbours.kneighbors(self._scaled(x[self.features].to_numpy()))  # type: ignore[attr-defined]
        return dist.mean(axis=1)

    def calibrate_novelty(self, x_known: pd.DataFrame, quantile: float = 0.99) -> float:
        self.novelty_threshold = float(np.quantile(self.novelty(x_known), quantile))
        return self.novelty_threshold

    @property
    def classes(self) -> list[str]:
        return list(self.estimator.classes_)  # type: ignore[attr-defined]

    def proba(self, x: pd.DataFrame) -> np.ndarray:
        return self.estimator.predict_proba(x[self.features].to_numpy())  # type: ignore[attr-defined]

    def predict(self, x: pd.DataFrame, threshold: float | None = None) -> tuple[list[str], np.ndarray]:
        p = self.proba(x)
        top = p.max(axis=1)
        labels = np.asarray(self.classes)[p.argmax(axis=1)]
        cut = self.unknown_threshold if threshold is None else threshold
        novel = self.novelty(x) > self.novelty_threshold
        out = [UNKNOWN if (conf < cut or far) else lbl for lbl, conf, far in zip(labels, top, novel)]
        return out, top

    def explain(self, x: pd.DataFrame) -> list[dict]:
        """Per-window reason for the decision."""
        p = self.proba(x)
        nov = self.novelty(x)
        rows = []
        for i in range(len(x)):
            order = np.argsort(p[i])[::-1]
            rows.append(
                {
                    "probabilities": {self.classes[j]: float(p[i, j]) for j in order},
                    "confidence": float(p[i, order[0]]),
                    "novelty": float(nov[i]),
                    "novelty_threshold": self.novelty_threshold,
                    "confidence_threshold": self.unknown_threshold,
                    "unknown_reason": (
                        "far from every training example"
                        if nov[i] > self.novelty_threshold
                        else "low confidence"
                        if p[i, order[0]] < self.unknown_threshold
                        else None
                    ),
                }
            )
        return rows

    def importances(self, x: pd.DataFrame, y: list[str]) -> pd.Series:
        est = self.estimator
        if hasattr(est, "feature_importances_"):
            return pd.Series(est.feature_importances_, index=self.features).sort_values(ascending=False)
        from sklearn.inspection import permutation_importance

        result = permutation_importance(est, x[self.features].to_numpy(), np.asarray(y), n_repeats=5, random_state=0)
        return pd.Series(result.importances_mean, index=self.features).sort_values(ascending=False)


def tune_threshold(clf: IncidentClassifier, x: pd.DataFrame, y: list[str]) -> tuple[float, float]:
    """Pick the confidence cut-off that maximises macro F1, where noise alerts are labelled `unknown`."""
    best = (0.0, -1.0)
    for thr in np.round(np.arange(0.30, 0.96, 0.01), 2):
        pred, _ = clf.predict(x, float(thr))
        score = f1_score(y, pred, labels=[*KNOWN, UNKNOWN], average="macro", zero_division=0)
        if score > best[1] + 1e-9:
            best = (float(thr), float(score))
    return best


def report(y_true: list[str], y_pred: list[str]) -> dict:
    """Per-class precision/recall/F1 over the classes present, macro F1 and a confusion matrix."""
    present = [c for c in LABELS if c in set(y_true) | set(y_pred)]
    scored = [c for c in present if c in set(y_true) and c != HELD_OUT]
    p, r, f, s = precision_recall_fscore_support(y_true, y_pred, labels=scored, zero_division=0)
    cm = confusion_matrix(y_true, y_pred, labels=present)
    held = [pr for tr, pr in zip(y_true, y_pred) if tr == HELD_OUT]
    known_true = [(t, q) for t, q in zip(y_true, y_pred) if t in KNOWN]
    return {
        "macro_f1": float(np.mean([f[i] for i, c in enumerate(scored) if c in KNOWN])) if scored else 0.0,
        "accuracy_known": float(np.mean([t == q for t, q in known_true])) if known_true else 0.0,
        "per_class": {
            c: {"precision": float(p[i]), "recall": float(r[i]), "f1": float(f[i]), "support": int(s[i])}
            for i, c in enumerate(scored)
        },
        "confusion": {"labels": present, "matrix": cm.tolist()},
        "held_out": {
            "category": HELD_OUT,
            "count": len(held),
            "routed_unknown": int(sum(h == UNKNOWN for h in held)),
            "share_unknown": float(np.mean([h == UNKNOWN for h in held])) if held else None,
            "predicted_as": pd.Series(held).value_counts().to_dict() if held else {},
        },
    }
