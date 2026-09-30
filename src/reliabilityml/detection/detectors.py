"""Two anomaly detectors that decide *when* something is wrong.

1. EWMA z-score: the largest per-signal z-score across services must exceed a threshold for
   N consecutive minutes.
2. Isolation Forest: trained on minutes from normal periods, using the same z-score vector
   as input, so it can react to combinations of moderate deviations.

Both produce the same alert records, which evaluate.py scores against ground truth.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest

from ..features.windows import z_matrix


@dataclass
class Alert:
    fired_at: pd.Timestamp  # minute the alert fired (after N consecutive anomalous minutes)
    started_at: pd.Timestamp  # first anomalous minute of the run
    ended_at: pd.Timestamp
    service: str
    signal: str
    peak: float


def runs_to_alerts(
    score: pd.Series,
    threshold: float,
    consecutive: int,
    culprit: pd.DataFrame,
    cooldown: int = 5,
) -> list[Alert]:
    """Turn a per-minute score into alerts.

    An alert fires on the Nth consecutive minute above threshold and stays open until the
    score has been below threshold for `cooldown` minutes. `culprit` holds, per minute, the
    column with the largest contribution, formatted "signal|service".
    """
    above = (score.to_numpy() > threshold).astype(np.int8)
    idx = score.index
    alerts: list[Alert] = []
    run = 0
    open_alert: dict | None = None
    quiet = 0
    for i, flag in enumerate(above):
        if flag:
            run += 1
            quiet = 0
            if open_alert is None and run >= consecutive:
                j = i - consecutive + 1
                key = culprit.iloc[i]
                signal, service = str(key).split("|")
                open_alert = {
                    "fired": idx[i],
                    "start": idx[j],
                    "service": service,
                    "signal": signal,
                    "peak": float(score.iloc[i]),
                }
            elif open_alert is not None:
                open_alert["peak"] = max(open_alert["peak"], float(score.iloc[i]))
        else:
            run = 0
            if open_alert is not None:
                quiet += 1
                if quiet >= cooldown:
                    alerts.append(
                        Alert(
                            open_alert["fired"],
                            open_alert["start"],
                            idx[i],
                            open_alert["service"],
                            open_alert["signal"],
                            open_alert["peak"],
                        )
                    )
                    open_alert = None
                    quiet = 0
    if open_alert is not None:
        alerts.append(
            Alert(
                open_alert["fired"],
                open_alert["start"],
                idx[-1],
                open_alert["service"],
                open_alert["signal"],
                open_alert["peak"],
            )
        )
    return alerts


class EwmaDetector:
    name = "ewma"

    def __init__(self, threshold: float = 5.0, consecutive: int = 2):
        self.threshold = threshold
        self.consecutive = consecutive

    def score(self, zmat: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
        return zmat.max(axis=1), zmat.idxmax(axis=1)

    def detect(self, zmat: pd.DataFrame) -> list[Alert]:
        score, culprit = self.score(zmat)
        return runs_to_alerts(score, self.threshold, self.consecutive, culprit)


class IsolationForestDetector:
    name = "isolation_forest"

    def __init__(self, threshold: float = 0.62, consecutive: int = 2, seed: int = 0):
        self.threshold = threshold
        self.consecutive = consecutive
        self.model = IsolationForest(n_estimators=200, max_samples=4096, contamination="auto", random_state=seed)
        self.columns: list[str] = []

    def fit(self, zmat: pd.DataFrame, normal_mask: pd.Series) -> IsolationForestDetector:
        self.columns = list(zmat.columns)
        # Isolation forests split on value ranges, so compress the long z tail with a log.
        self.model.fit(np.log1p(zmat.loc[normal_mask, self.columns].to_numpy()))
        return self

    def score(self, zmat: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
        x = np.log1p(zmat[self.columns].to_numpy())
        raw = -self.model.score_samples(x)  # higher = more anomalous, roughly 0.3-0.8
        return pd.Series(raw, index=zmat.index), zmat[self.columns].idxmax(axis=1)

    def detect(self, zmat: pd.DataFrame) -> list[Alert]:
        score, culprit = self.score(zmat)
        return runs_to_alerts(score, self.threshold, self.consecutive, culprit)


def normal_minutes(index: pd.DatetimeIndex, faults: pd.DataFrame, margin_min: int = 30) -> pd.Series:
    mask = pd.Series(True, index=index)
    for f in faults.itertuples():
        mask[
            (index >= f.start - pd.Timedelta(minutes=margin_min)) & (index <= f.end + pd.Timedelta(minutes=margin_min))
        ] = False
    mask.iloc[:120] = False  # EWMA warm-up
    return mask


__all__ = ["Alert", "EwmaDetector", "IsolationForestDetector", "normal_minutes", "z_matrix"]
