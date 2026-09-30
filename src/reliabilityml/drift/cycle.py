"""Drift detection (PSI) and the gated retraining cycle.

A "platform upgrade" shifts baselines permanently (+20% latency, a different request mix). The
cycle: measure drift -> if significant, retrain on recent labelled data -> compare the candidate
with the production model on the same held-out data -> promote only if macro F1 is better AND the
false-alarm rate is not worse -> record the decision.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..classifier.model import KNOWN, IncidentClassifier, report
from ..detection.detectors import runs_to_alerts
from ..detection.evaluate import match
from ..features.windows import z_matrix, z_scores
from ..generator.topology import SERVICE_NAMES

PSI_MODERATE = 0.1
PSI_SIGNIFICANT = 0.25
DRIFT_SIGNALS = ["latency_p50", "latency_p95", "request_rate", "cpu_utilization", "error_rate"]
RETRAIN_DAYS = 6  # first days of the post-upgrade period used as fresh labelled training data


def psi(expected: np.ndarray, actual: np.ndarray, bins: int = 10) -> float:
    """Population Stability Index with bins from the expected (training) quantiles."""
    expected = expected[np.isfinite(expected)]
    actual = actual[np.isfinite(actual)]
    edges = np.unique(np.quantile(expected, np.linspace(0, 1, bins + 1)))
    if len(edges) < 3:
        return 0.0
    edges[0], edges[-1] = -np.inf, np.inf
    e = np.histogram(expected, edges)[0] / len(expected)
    a = np.histogram(actual, edges)[0] / len(actual)
    e, a = np.clip(e, 1e-6, None), np.clip(a, 1e-6, None)
    return float(np.sum((a - e) * np.log(a / e)))


def level(value: float) -> str:
    return "significant" if value > PSI_SIGNIFICANT else "moderate" if value >= PSI_MODERATE else "stable"


def signal_drift(reference, current) -> list[dict]:
    rows = []
    for signal in DRIFT_SIGNALS:
        for svc in SERVICE_NAMES:
            ref = reference.signal(signal)[svc].to_numpy(dtype=float)
            cur = current.signal(signal)[svc].to_numpy(dtype=float)
            value = psi(ref, cur)
            rows.append({"signal": signal, "service": svc, "psi": round(value, 4), "level": level(value)})
    return sorted(rows, key=lambda r: -r["psi"])


def _windows(tel, md, faults, offsets=(1, 2, 3, 4, 6)):
    from ..classifier.features import frame_for

    times, labels, days = [], [], []
    for f in faults.itertuples():
        for off in offsets:
            times.append(f.start + pd.Timedelta(minutes=off))
            labels.append(f.category)
            days.append((f.start - tel.config.start).days)
    return frame_for(md, times, [None] * len(times)), np.array(labels), np.array(days)


def _false_alarm_rate(clf: IncidentClassifier, x_noise: pd.DataFrame) -> float | None:
    if len(x_noise) == 0:
        return None
    return float(np.mean([p != "unknown" for p in clf.predict(x_noise)[0]]))


def stage_drift(ds, cls: dict, out, registry) -> dict:
    from ..classifier.features import frame_for
    from ..pipelines.build import OFFSETS_MIN, _fault_windows, write_json

    production: IncidentClassifier = cls["model"]
    drift_rows = signal_drift(ds.m_train, ds.m_upgrade)
    max_psi = max(r["psi"] for r in drift_rows)
    significant = [r for r in drift_rows if r["level"] == "significant"]

    # Alerts in the upgraded period, with the production detector settings.
    op = cls["result"].get("detector_op") or {"threshold": 10.0, "consecutive": 3}
    zup = z_matrix(z_scores(ds.m_upgrade))
    alerts = runs_to_alerts(zup.max(axis=1), op["threshold"], op["consecutive"], zup.idxmax(axis=1))
    _, is_true = match(alerts, ds.upgrade.faults)
    noise = [a for a, t in zip(alerts, is_true) if not t]
    noise_day = np.array([(a.fired_at - ds.upgrade.config.start).days for a in noise])
    x_noise = frame_for(ds.m_upgrade, [a.fired_at for a in noise], [a.service for a in noise])

    faults = ds.upgrade.faults[ds.upgrade.faults.category.isin(KNOWN)]
    x_up, y_up, day_up = _windows(ds.upgrade, ds.m_upgrade, faults, OFFSETS_MIN)
    recent, holdout = day_up < RETRAIN_DAYS, day_up >= RETRAIN_DAYS
    x_eval, y_eval = x_up[holdout], list(y_up[holdout])
    x_noise_eval = x_noise[noise_day >= RETRAIN_DAYS] if len(noise) else x_noise

    # Confidence and unknown share on the upgraded platform, before retraining. "Before" uses the same
    # kind of windows (known-category fault windows) from the pre-upgrade test week.
    labels_prod, conf_prod = production.predict(x_up)
    x_before, _, _ = _fault_windows(ds.test, ds.m_test, ds.test.faults[ds.test.faults.category.isin(KNOWN)])
    labels_before, conf_before = production.predict(x_before)
    x_train_windows, _, _ = _fault_windows(ds.train, ds.m_train, ds.train.faults)
    feature_psi = sorted(
        (
            {"feature": f, "psi": round(psi(x_train_windows[f].to_numpy(float), x_up[f].to_numpy(float), bins=5), 4)}
            for f in production.features
        ),
        key=lambda r: -float(r["psi"]),  # type: ignore[arg-type]
    )
    model_health = {
        "mean_confidence_before": float(np.mean(conf_before)),
        "unknown_share_before": float(np.mean([p == "unknown" for p in labels_before])),
        "mean_confidence_after_upgrade": float(np.mean(conf_prod)),
        "unknown_share_after_upgrade": float(np.mean([p == "unknown" for p in labels_prod])),
    }

    decision = {"drift_detected": max_psi > PSI_SIGNIFICANT, "retrained": False, "promoted": False}
    comparison = {}
    if decision["drift_detected"]:
        x_train, y_train, _ = _fault_windows(ds.train, ds.m_train, ds.train.faults)
        x_new = pd.concat([x_train, x_up[recent]], ignore_index=True)
        y_new = list(y_train) + list(y_up[recent])
        candidate = IncidentClassifier(
            production.kind, production.params, unknown_threshold=production.unknown_threshold
        ).fit(x_new, y_new)
        dist, _ = candidate.neighbours.kneighbors(
            candidate._scaled(x_new[candidate.features].to_numpy()), n_neighbors=6
        )
        candidate.novelty_threshold = float(np.quantile(dist[:, 1:].mean(axis=1), 0.99))
        decision["retrained"] = True
        for name, model in (("production", production), ("candidate", candidate)):
            rep = report(y_eval, model.predict(x_eval)[0])
            comparison[name] = {
                "macro_f1": rep["macro_f1"],
                "per_class": rep["per_class"],
                "false_alarm_rate": _false_alarm_rate(model, x_noise_eval),
                "unknown_share": float(np.mean([p == "unknown" for p in model.predict(x_eval)[0]])),
            }
        prod, cand = comparison["production"], comparison["candidate"]
        better_f1 = cand["macro_f1"] > prod["macro_f1"]
        fa_ok = (cand["false_alarm_rate"] or 0) <= (prod["false_alarm_rate"] or 0)
        decision.update(
            {"better_macro_f1": better_f1, "false_alarm_not_worse": fa_ok, "promoted": bool(better_f1 and fa_ok)}
        )
        params = {
            "model_type": candidate.kind,
            **candidate.params,
            "trigger": "psi_drift",
            "retrain_data": f"train + first {RETRAIN_DAYS} post-upgrade days",
        }
        metrics = {
            "candidate_macro_f1": cand["macro_f1"],
            "production_macro_f1": prod["macro_f1"],
            "max_psi": max_psi,
            "candidate_false_alarm_rate": cand["false_alarm_rate"] or 0.0,
            "production_false_alarm_rate": prod["false_alarm_rate"] or 0.0,
        }
        record = registry.log_run(
            "retrain-after-drift",
            params,
            metrics,
            {"decision": "promoted" if decision["promoted"] else "rejected"},
            {},
            model=candidate,
        )
        version = registry.register(record, candidate, alias="staging")
        decision["candidate_version"] = version
        if decision["promoted"]:
            registry.set_alias("production", version)
        decision["production_version"] = registry.alias("production")

    result = {
        "psi_thresholds": {"moderate": PSI_MODERATE, "significant": PSI_SIGNIFICANT},
        "signals": drift_rows,
        "max_psi": max_psi,
        "significant_signals": len(significant),
        "feature_psi": feature_psi,
        "feature_psi_median": float(np.median([r["psi"] for r in feature_psi])),
        "feature_psi_significant": sum(float(r["psi"]) > PSI_SIGNIFICANT for r in feature_psi),  # type: ignore[arg-type]
        "model_health": model_health,
        "evaluation": {
            "holdout_windows": int(holdout.sum()),
            "noise_alerts": int(len(x_noise_eval)),
            "retrain_windows": int(recent.sum()),
        },
        "comparison": comparison,
        "decision": decision,
        "headline": {
            "max_psi": round(max_psi, 3),
            "drift_detected": decision["drift_detected"],
            "promoted": decision["promoted"],
            "candidate_macro_f1": comparison.get("candidate", {}).get("macro_f1"),
            "production_macro_f1": comparison.get("production", {}).get("macro_f1"),
        },
    }
    write_json(out / "reports" / "drift.json", result)
    return result
