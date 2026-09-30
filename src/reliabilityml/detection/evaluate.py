"""Score alerts against injected faults: recall, time to detect, false alerts per day, precision."""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from .detectors import Alert

GRACE_AFTER = pd.Timedelta(minutes=15)  # alerts shortly after a fault ends belong to its recovery


@dataclass
class DetectionReport:
    recall: float
    median_ttd_min: float | None
    false_alerts_per_day: float
    precision: float
    alerts: int
    true_alerts: int
    false_alerts: int
    detected: int
    incidents: int
    per_category: dict[str, dict]

    def to_dict(self) -> dict:
        return asdict(self)


def match(alerts: list[Alert], faults: pd.DataFrame) -> tuple[dict[str, Alert | None], list[bool]]:
    """First alert that fired during each fault, and whether each alert overlaps any fault."""
    first: dict[str, Alert | None] = {f: None for f in faults["fault_id"]}
    is_true: list[bool] = []
    for alert in alerts:
        hit = False
        for f in faults.itertuples():
            # counts if the alert fires while the fault is active (or during recovery), or
            # if an alert that started before the fault is still open when it begins
            if f.start <= alert.fired_at <= f.end + GRACE_AFTER or (alert.fired_at < f.start <= alert.ended_at):
                hit = True
                current = first[f.fault_id]
                if current is None or alert.fired_at < current.fired_at:
                    first[f.fault_id] = alert
        is_true.append(hit)
    return first, is_true


def evaluate(alerts: list[Alert], faults: pd.DataFrame, days: float) -> DetectionReport:
    first, is_true = match(alerts, faults)
    ttd = {}
    for f in faults.itertuples():
        a = first[f.fault_id]
        if a is not None:
            ttd[f.fault_id] = max(0.0, (a.fired_at - f.start).total_seconds() / 60)
    per_category = {}
    for category, group in faults.groupby("category"):
        ids = list(group["fault_id"])
        vals = [ttd[i] for i in ids if i in ttd]
        per_category[category] = {
            "incidents": len(ids),
            "detected": len(vals),
            "recall": len(vals) / len(ids),
            "median_ttd_min": float(np.median(vals)) if vals else None,
        }
    true_alerts = sum(is_true)
    false_alerts = len(alerts) - true_alerts
    return DetectionReport(
        recall=len(ttd) / max(len(faults), 1),
        median_ttd_min=float(np.median(list(ttd.values()))) if ttd else None,
        false_alerts_per_day=false_alerts / days,
        precision=true_alerts / len(alerts) if alerts else 0.0,
        alerts=len(alerts),
        true_alerts=true_alerts,
        false_alerts=false_alerts,
        detected=len(ttd),
        incidents=len(faults),
        per_category=per_category,
    )


def sweep(detector_factory, zmat: pd.DataFrame, faults: pd.DataFrame, days: float, thresholds, consecutive=(1, 2, 3)):
    """Precision vs. time-to-detect trade-off across thresholds."""
    rows = []
    for n in consecutive:
        for thr in thresholds:
            det = detector_factory(thr, n)
            report = evaluate(det.detect(zmat), faults, days)
            rows.append(
                {
                    "threshold": float(thr),
                    "consecutive": n,
                    **{k: v for k, v in report.to_dict().items() if k != "per_category"},
                }
            )
    return pd.DataFrame(rows)


def choose_operating_point(curve: pd.DataFrame, min_recall: float = 0.9) -> pd.Series:
    """Fewest false alerts among settings that keep recall high; ties broken by faster detection."""
    ok = curve[curve["recall"] >= min_recall]
    if ok.empty:
        ok = curve[curve["recall"] == curve["recall"].max()]
    ok = ok.assign(ttd=ok["median_ttd_min"].fillna(1e9))
    return ok.sort_values(["false_alerts_per_day", "ttd"]).iloc[0]
