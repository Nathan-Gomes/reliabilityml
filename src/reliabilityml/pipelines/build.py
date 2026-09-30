"""End-to-end pipeline: data -> detection -> classifier -> SLOs -> RAG -> drift -> gate.

`python -m reliabilityml.pipelines.build` writes everything the API, README and dashboards read
into artifacts/. Each stage is a plain function so it can run under Prefect (see flows.py) or alone.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ..classifier import rules
from ..classifier.features import FEATURE_VERSION, frame_for
from ..classifier.model import KNOWN, IncidentClassifier, report, tune_threshold
from ..classifier.registry import Registry
from ..detection.detectors import IsolationForestDetector, normal_minutes, runs_to_alerts
from ..detection.evaluate import choose_operating_point, evaluate, match
from ..features.windows import MinuteData, minute_data, z_matrix, z_scores
from ..generator.simulate import Telemetry, default_ops, default_test, default_train, default_upgrade, simulate
from ..generator.topology import SERVICE_NAMES
from ..slo import engine as slo

log = logging.getLogger("reliabilityml.pipeline")
ROOT = Path(__file__).resolve().parents[3]
ARTIFACTS = ROOT / "artifacts"
OFFSETS_MIN = [1, 2, 3, 4, 6]  # window positions after fault onset used for training/eval windows
VALIDATION_FROM_DAY = 22
EWMA_THRESHOLDS = [3, 4, 5, 6, 7, 8, 10, 12, 14, 18, 24]
CANDIDATES: list[tuple[str, dict[str, Any]]] = [
    ("random_forest", {"n_estimators": 300, "max_depth": None, "min_samples_leaf": 1}),
    ("random_forest", {"n_estimators": 300, "max_depth": 8, "min_samples_leaf": 2}),
    ("gradient_boosting", {"max_depth": 3, "learning_rate": 0.1, "max_iter": 200}),
    ("gradient_boosting", {"max_depth": None, "learning_rate": 0.05, "max_iter": 300}),
]


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, default=_default))


def _default(o):
    if isinstance(o, (pd.Timestamp,)):
        return o.isoformat()
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return None if not np.isfinite(o) else float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    return str(o)


@dataclass
class Datasets:
    train: Telemetry
    test: Telemetry
    upgrade: Telemetry
    ops: Telemetry
    m_train: MinuteData
    m_test: MinuteData
    m_upgrade: MinuteData
    m_ops: MinuteData


# ------------------------------------------------------------------ data


def stage_data(out: Path) -> Datasets:
    t = time.time()
    train, test, upgrade = simulate(default_train()), simulate(default_test()), simulate(default_upgrade())
    ops = simulate(default_ops())
    data_dir = out / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    for name, tel in (("train", train), ("test", test), ("upgrade", upgrade), ("ops", ops)):
        tel.faults.drop(columns=["start_step", "end_step", "deploy_step", "nearby_deploy_step"]).to_csv(
            data_dir / f"{name}_ground_truth.csv", index=False
        )
        tel.deploys.to_csv(data_dir / f"{name}_deploys.csv", index=False)
    # Keep 1-minute rollups of the test week (and the last 23 training days for SLO windows).
    md_train, md_test, md_up, md_ops = minute_data(train), minute_data(test), minute_data(upgrade), minute_data(ops)
    for name, md in (("test", md_test), ("ops", md_ops)):
        long = pd.concat({k: v for k, v in md.frames.items()}, axis=1)
        long.columns = [f"{sig}|{svc}" for sig, svc in long.columns]
        long.astype("float32").to_parquet(data_dir / f"{name}_minutes.parquet")
    test.logs.to_csv(data_dir / "test_logs.csv", index=False)
    log.info("data: %.1fs, %d/%d/%d faults", time.time() - t, len(train.faults), len(test.faults), len(upgrade.faults))
    return Datasets(train, test, upgrade, ops, md_train, md_test, md_up, md_ops)


# ------------------------------------------------------------------ detection


def stage_detection(ds: Datasets, out: Path) -> dict:
    ztr, zte = z_matrix(z_scores(ds.m_train)), z_matrix(z_scores(ds.m_test))
    days_tr, days_te = ds.train.config.days, ds.test.config.days
    s_tr, c_tr = ztr.max(axis=1), ztr.idxmax(axis=1)
    s_te, c_te = zte.max(axis=1), zte.idxmax(axis=1)

    def curve(score, culprit, faults, days, thresholds, consecutive=(1, 2, 3)):
        rows = []
        for n in consecutive:
            for thr in thresholds:
                r = evaluate(runs_to_alerts(score, float(thr), n, culprit), faults, days).to_dict()
                r.pop("per_category")
                rows.append({"threshold": float(thr), "consecutive": n, **r})
        return pd.DataFrame(rows)

    ewma_train = curve(s_tr, c_tr, ds.train.faults, days_tr, EWMA_THRESHOLDS)
    op = choose_operating_point(ewma_train, min_recall=0.93)
    ewma_test = curve(s_te, c_te, ds.test.faults, days_te, EWMA_THRESHOLDS)

    iso = IsolationForestDetector().fit(ztr, normal_minutes(ztr.index, ds.train.faults))
    si_tr, ci_tr = iso.score(ztr)
    si_te, ci_te = iso.score(zte)
    iso_thresholds = list(np.quantile(si_tr, [0.95, 0.97, 0.98, 0.99, 0.995, 0.998, 0.999]))
    iso_train = curve(si_tr, ci_tr, ds.train.faults, days_tr, iso_thresholds)
    iso_op = choose_operating_point(iso_train, min_recall=0.93)
    iso_test = curve(si_te, ci_te, ds.test.faults, days_te, iso_thresholds)

    alerts_te = runs_to_alerts(s_te, float(op.threshold), int(op.consecutive), c_te)
    alerts_tr = runs_to_alerts(s_tr, float(op.threshold), int(op.consecutive), c_tr)
    rep_te = evaluate(alerts_te, ds.test.faults, days_te)
    iso_alerts_te = runs_to_alerts(si_te, float(iso_op.threshold), int(iso_op.consecutive), ci_te)
    iso_rep_te = evaluate(iso_alerts_te, ds.test.faults, days_te)

    result = {
        "operating_point": {
            "detector": "ewma",
            "threshold": float(op.threshold),
            "consecutive": int(op.consecutive),
            "chosen_on": "training period, fewest false alerts with recall >= 93%",
        },
        "test": rep_te.to_dict(),
        "isolation_forest": {
            "operating_point": {"threshold": float(iso_op.threshold), "consecutive": int(iso_op.consecutive)},
            "test": iso_rep_te.to_dict(),
        },
        "curves": {
            "ewma_train": ewma_train.to_dict("records"),
            "ewma_test": ewma_test.to_dict("records"),
            "iforest_train": iso_train.to_dict("records"),
            "iforest_test": iso_test.to_dict("records"),
        },
    }
    write_json(out / "reports" / "detection.json", result)
    return {"result": result, "alerts_train": alerts_tr, "alerts_test": alerts_te, "z_test": zte}


# ------------------------------------------------------------------ classifier


def _fault_windows(tel: Telemetry, md: MinuteData, faults: pd.DataFrame):
    times, labels, days = [], [], []
    for f in faults.itertuples():
        for off in OFFSETS_MIN:
            times.append(f.start + pd.Timedelta(minutes=off))
            labels.append(f.category)
            days.append((f.start - tel.config.start).days)
    return frame_for(md, times, [None] * len(times)), np.array(labels), np.array(days)


def stage_classifier(ds: Datasets, det: dict, out: Path, registry: Registry) -> dict:
    reports_dir = out / "reports"
    x, y, day = _fault_windows(ds.train, ds.m_train, ds.train.faults)
    _, is_true = match(det["alerts_train"], ds.train.faults)
    noise = [a for a, t in zip(det["alerts_train"], is_true) if not t]
    xn = frame_for(ds.m_train, [a.fired_at for a in noise], [a.service for a in noise])
    dn = np.array([(a.fired_at - ds.train.config.start).days for a in noise])
    fit_mask, val_mask = day < VALIDATION_FROM_DAY, day >= VALIDATION_FROM_DAY
    x_val = pd.concat([x[val_mask], xn[dn >= VALIDATION_FROM_DAY]], ignore_index=True)
    y_val = list(y[val_mask]) + ["unknown"] * int((dn >= VALIDATION_FROM_DAY).sum())

    # Model selection on the time-based validation split.
    selection: list[dict[str, Any]] = []
    for kind, params in CANDIDATES:
        clf = IncidentClassifier(kind, params).fit(x[fit_mask], list(y[fit_mask]))
        clf.calibrate_novelty(x[val_mask])
        thr, score = tune_threshold(clf, x_val, y_val)
        selection.append({"kind": kind, "params": params, "threshold": thr, "val_macro_f1_with_unknown": score})
    rules_val = report(y_val, rules.predict(x_val))
    best = max(selection, key=lambda r: r["val_macro_f1_with_unknown"])

    # Final model: refit on all training windows with the chosen settings.
    clf = IncidentClassifier(best["kind"], best["params"], unknown_threshold=best["threshold"]).fit(x, list(y))
    dist, _ = clf.neighbours.kneighbors(clf._scaled(x[clf.features].to_numpy()), n_neighbors=6)
    clf.novelty_threshold = float(np.quantile(dist[:, 1:].mean(axis=1), 0.99))  # leave-self-out distances

    # Test evaluation, three ways.
    xt, yt, _ = _fault_windows(ds.test, ds.m_test, ds.test.faults[ds.test.faults.category.isin(KNOWN)])
    model_windows = report(list(yt), clf.predict(xt)[0])
    rules_windows = report(list(yt), rules.predict(xt))

    first, is_t = match(det["alerts_test"], ds.test.faults)
    faults = ds.test.faults.set_index("fault_id")
    alert_rows = []
    for alert, true in zip(det["alerts_test"], is_t):
        label = "unknown"
        if true:
            hits = [fid for fid, a in first.items() if a is alert]
            owner = hits[0] if hits else _owner(alert, ds.test.faults)
            label = faults.loc[owner, "category"] if owner else "unknown"
        alert_rows.append((alert, label))
    xa = frame_for(ds.m_test, [a.fired_at for a, _ in alert_rows], [a.service for a, _ in alert_rows])
    ya = [lbl for _, lbl in alert_rows]
    pa, conf = clf.predict(xa)
    ra = rules.predict(xa)
    model_alerts, rules_alerts = report(ya, pa), report(ya, ra)
    false_alerts = [(p, r) for (p, r), lbl, (a, t) in zip(zip(pa, ra), ya, zip(det["alerts_test"], is_t)) if not t]
    held = [(p, r) for p, r, lbl in zip(pa, ra, ya) if lbl == "memory_leak"]

    importance = clf.importances(x, list(y)).head(15)
    _plots(reports_dir, model_alerts, importance)
    metrics = {
        "test_macro_f1_windows": model_windows["macro_f1"],
        "test_macro_f1_alerts": model_alerts["macro_f1"],
        "rules_macro_f1_windows": rules_windows["macro_f1"],
        "rules_macro_f1_alerts": rules_alerts["macro_f1"],
        "held_out_share_unknown": _share(held, 0),
        "rules_held_out_share_unknown": _share(held, 1),
        "false_alerts_routed_unknown": _share(false_alerts, 0),
        "val_macro_f1_with_unknown": best["val_macro_f1_with_unknown"],
        "unknown_threshold": clf.unknown_threshold,
        "novelty_threshold": clf.novelty_threshold,
    }
    params = {
        "model_type": best["kind"],
        **best["params"],
        "feature_set": FEATURE_VERSION,
        "data_seed_train": ds.train.config.seed,
        "data_seed_test": ds.test.config.seed,
    }
    tags = {"dataset_version": f"train-s{ds.train.config.seed}-test-s{ds.test.config.seed}"}
    registry.log_run(
        "rules-baseline",
        {"model_type": "rules", "feature_set": FEATURE_VERSION},
        {"test_macro_f1_windows": rules_windows["macro_f1"], "test_macro_f1_alerts": rules_alerts["macro_f1"]},
        tags,
        {},
    )
    record = registry.log_run(
        f"{best['kind']}-{FEATURE_VERSION}",
        params,
        metrics,
        tags,
        {"confusion": reports_dir / "confusion_matrix.png", "importance": reports_dir / "feature_importance.png"},
        model=clf,
    )
    version = registry.register(record, clf, alias="staging")
    registry.set_alias("production", version)  # first model: promote directly; later runs go through the gate

    result = {
        "detector_op": det["result"]["operating_point"],
        "model": {
            "kind": best["kind"],
            "params": best["params"],
            "version": version,
            "run_id": record.run_id,
            "unknown_threshold": clf.unknown_threshold,
            "novelty_threshold": clf.novelty_threshold,
            "feature_set": FEATURE_VERSION,
        },
        "selection": selection,
        "rules_validation": {"macro_f1": rules_val["macro_f1"]},
        "training": {
            "windows": int(len(x)),
            "noise_alerts": int(len(noise)),
            "classes": pd.Series(y).value_counts().to_dict(),
            "validation_from_day": VALIDATION_FROM_DAY,
        },
        "test_windows": {"model": model_windows, "rules": rules_windows},
        "test_alerts": {"model": model_alerts, "rules": rules_alerts, "alerts": len(ya)},
        "held_out": {
            "alerts": len(held),
            "model_unknown": sum(p == "unknown" for p, _ in held),
            "rules_unknown": sum(r == "unknown" for _, r in held),
            "rules_memory_leak": sum(r == "memory_leak" for _, r in held),
            "model_predicted": pd.Series([p for p, _ in held]).value_counts().to_dict() if held else {},
        },
        "false_alerts": {
            "count": len(false_alerts),
            "model_unknown": sum(p == "unknown" for p, _ in false_alerts),
            "rules_unknown": sum(r == "unknown" for _, r in false_alerts),
        },
        "importance": importance.round(4).to_dict(),
        "metrics": metrics,
    }
    write_json(reports_dir / "classifier.json", result)
    return {"result": result, "model": clf, "alerts": alert_rows, "predictions": list(zip(pa, conf)), "x_alerts": xa}


def _owner(alert, faults: pd.DataFrame):
    for f in faults.itertuples():
        if f.start - pd.Timedelta(minutes=1) <= alert.fired_at <= f.end + pd.Timedelta(minutes=15) or (
            alert.fired_at < f.start <= alert.ended_at
        ):
            return f.fault_id
    return None


def _share(pairs, index):
    return float(np.mean([p[index] == "unknown" for p in pairs])) if pairs else None


def _plots(folder: Path, rep: dict, importance: pd.Series) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    folder.mkdir(parents=True, exist_ok=True)
    labels, matrix = rep["confusion"]["labels"], np.array(rep["confusion"]["matrix"])
    fig, ax = plt.subplots(figsize=(6.5, 5.5))
    ax.imshow(matrix, cmap="Blues")
    ax.set_xticks(range(len(labels)), [lbl.replace("_", "\n") for lbl in labels], fontsize=8)
    ax.set_yticks(range(len(labels)), labels, fontsize=8)
    for i in range(len(labels)):
        for j in range(len(labels)):
            ax.text(
                j,
                i,
                matrix[i, j],
                ha="center",
                va="center",
                color="white" if matrix[i, j] > matrix.max() / 2 else "black",
            )
    ax.set_xlabel("Predicted")
    ax.set_ylabel("Actual")
    ax.set_title("Incident classifier, test-week alerts")
    fig.tight_layout()
    fig.savefig(folder / "confusion_matrix.png", dpi=130)
    plt.close(fig)
    fig, ax = plt.subplots(figsize=(6.5, 5))
    importance[::-1].plot.barh(ax=ax, color="#2a78d6")
    ax.set_title("Feature importance (top 15)")
    fig.tight_layout()
    fig.savefig(folder / "feature_importance.png", dpi=130)
    plt.close(fig)


# ------------------------------------------------------------------ incidents for the API


def stage_incidents(ds: Datasets, det: dict, cls: dict, out: Path) -> list[dict]:
    """The test week's alerts with predictions, evidence and ground truth, as the API serves them."""
    clf: IncidentClassifier = cls["model"]
    explanations = clf.explain(cls["x_alerts"])
    faults = ds.test.faults
    incidents = []
    for i, ((alert, _label), (pred, conf), expl) in enumerate(zip(cls["alerts"], cls["predictions"], explanations)):
        feats = cls["x_alerts"].iloc[i]
        rule, why = rules.rule_for(feats)
        owner = _owner(alert, faults)
        truth = faults.set_index("fault_id").loc[owner] if owner else None
        incidents.append(
            {
                "id": f"INC-{i + 1:03d}",
                "fired_at": alert.fired_at,
                "started_at": alert.started_at,
                "ended_at": alert.ended_at,
                "service": alert.service,
                "signal": alert.signal,
                "peak_z": round(alert.peak, 1),
                "predicted": pred,
                "confidence": round(float(conf), 3),
                "explanation": expl,
                "rules_prediction": rule,
                "rules_reason": why,
                "evidence": _evidence(feats),
                "ground_truth": None
                if truth is None
                else {
                    "fault_id": owner,
                    "category": truth["category"],
                    "service": truth["service"],
                    "start": truth["start"],
                    "end": truth["end"],
                    "detail": truth["detail"],
                },
                "status": "resolved",
            }
        )
    write_json(out / "incidents.json", incidents)
    return incidents


def _evidence(f: pd.Series) -> list[dict]:
    items = []

    def add(label, value, fmt, strong):
        items.append({"signal": label, "value": fmt.format(value), "notable": bool(strong)})

    add("DB connection saturation", f["db_saturation_ratio"], "{:.0%} of pool", f["db_saturation_ratio"] >= 0.85)
    add("Edge traffic vs baseline", f["edge_traffic_delta"], "{:+.0%}", abs(f["edge_traffic_delta"]) >= 0.4)
    add("Services with errors up", f["services_with_errors"], "{:.0f}", f["services_with_errors"] >= 1)
    add(
        "Minutes since deploy on error origin",
        f["min_since_deploy_error_origin"],
        "{:.0f} min",
        f["min_since_deploy_error_origin"] <= 15,
    )
    add("Memory change, last hour", f["memory_slope_60m"], "{:+.0%}", f["memory_slope_60m"] >= 0.08)
    add("ERROR log volume vs baseline", f["error_log_ratio"], "{:+.2f} (log ratio)", f["error_log_ratio"] >= 1)
    add("Orders p99 vs baseline", f["orders_latency_p99_delta"], "{:+.0%}", f["orders_latency_p99_delta"] >= 0.5)
    add("Frontend p99 vs baseline", f["web_latency_p99_delta"], "{:+.0%}", f["web_latency_p99_delta"] >= 0.5)
    return items


# ------------------------------------------------------------------ SLOs


def stage_slo(ds: Datasets, out: Path) -> dict:
    """SLOs over the 30-day production replay (realistic incident rate), ending with the test week."""
    md = ds.m_ops
    services = {}
    alerts = []
    last_week = md.index >= md.index[-1] - pd.Timedelta(days=7)
    for svc in [s for s in SERVICE_NAMES if s != "postgres-db"]:
        sli = slo.sli_minutes(
            md.signal("request_rate")[svc],
            md.signal("error_rate")[svc],
            md.signal("latency_p50")[svc],
            md.signal("latency_p95")[svc],
        )
        avail = slo.status(sli, "bad_availability", slo.AVAILABILITY)
        lat = slo.status(sli, "bad_latency", slo.LATENCY)
        br1h = slo.rolling_burn(sli, "bad_availability", "1h", slo.AVAILABILITY)
        br6h = slo.rolling_burn(sli, "bad_availability", "6h", slo.AVAILABILITY)
        budget = 1 - (sli["bad_availability"].cumsum() / sli["total"].cumsum()) / slo.AVAILABILITY.allowed_error_rate
        series = pd.DataFrame({"burn_1h": br1h, "burn_6h": br6h, "budget_remaining": budget})
        # Skip the first two days: a cumulative budget over a tiny denominator is just noise.
        series = series.resample("30min").max().loc[md.index[0] + pd.Timedelta(days=2) :]
        for sli_name, column, target in (
            ("availability", "bad_availability", slo.AVAILABILITY),
            ("latency", "bad_latency", slo.LATENCY),
        ):
            for row in slo.burn_alerts(sli, column, target).to_dict("records"):
                alerts.append({"service": svc, "sli": sli_name, **row})
        services[svc] = {
            "availability": avail,
            "latency": lat,
            "series": {
                "ts": [t.isoformat() for t in series.index],
                "burn_1h": series["burn_1h"].round(3).fillna(0).tolist(),
                "burn_6h": series["burn_6h"].round(3).fillna(0).tolist(),
                "budget_remaining": series["budget_remaining"].round(4).fillna(1).tolist(),
            },
        }
    del last_week
    incidents = ds.ops.faults[["fault_id", "category", "service", "start", "end", "detail"]].to_dict("records")
    result = {
        "window": {"start": md.index[0], "end": md.index[-1]},
        "timeline": "production replay: 30 days, realistic incident rate",
        "rules": [
            {
                "severity": r.severity,
                "long": r.long,
                "short": r.short,
                "threshold": r.threshold,
                "budget_share": r.budget_share,
            }
            for r in slo.BURN_RULES
        ],
        "services": services,
        "alerts": sorted(alerts, key=lambda a: a["start"]),
        "incidents": incidents,
    }
    write_json(out / "reports" / "slo.json", result)
    return result


# ------------------------------------------------------------------ entry point


def run(out: Path = ARTIFACTS, stages: tuple[str, ...] = ("all",)) -> dict:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    out.mkdir(parents=True, exist_ok=True)
    started = time.time()
    registry = Registry(out / "registry")
    ds = stage_data(out)
    det = stage_detection(ds, out)
    log.info("detection done %.0fs", time.time() - started)
    cls = stage_classifier(ds, det, out, registry)
    log.info("classifier done %.0fs", time.time() - started)
    stage_incidents(ds, det, cls, out)
    slo_result = stage_slo(ds, out)
    from ..drift.cycle import stage_drift
    from ..gate.validate import stage_gate
    from ..rag.evaluate import stage_rag

    rag_result = stage_rag(out, registry)
    drift_result = stage_drift(ds, cls, out, registry)
    gate_result = stage_gate(out)
    summary = _summary(det["result"], cls["result"], slo_result, rag_result, drift_result, gate_result)
    write_json(out / "summary.json", summary)
    from ..cloud import upload

    upload(out)
    log.info("pipeline finished in %.0fs", time.time() - started)
    return summary


def _summary(det, cls, slo_result, rag, drift, gate) -> dict:
    t = det["test"]
    return {
        "detection": {
            "recall": t["recall"],
            "median_ttd_min": t["median_ttd_min"],
            "false_alerts_per_day": t["false_alerts_per_day"],
            "precision": t["precision"],
            "isolation_forest": {
                k: det["isolation_forest"]["test"][k]
                for k in ("recall", "median_ttd_min", "false_alerts_per_day", "precision")
            },
        },
        "classifier": {**{k: cls["metrics"][k] for k in cls["metrics"]}, "model": cls["model"]["kind"]},
        "slo": {
            svc: {
                "availability": round(v["availability"]["sli"], 5),
                "budget_remaining": round(v["availability"]["budget_remaining"], 3),
            }
            for svc, v in slo_result["services"].items()
        },
        "rag": rag.get("headline", {}),
        "drift": drift.get("headline", {}),
        "gate": {name: g["passed"] for name, g in gate.get("examples", {}).items()},
    }


if __name__ == "__main__":
    run()
