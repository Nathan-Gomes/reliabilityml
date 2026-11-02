from dataclasses import replace

import pandas as pd

from reliabilityml.classifier.registry import Registry
from reliabilityml.classifier.rules import rule_for
from reliabilityml.detection.detectors import EwmaDetector, runs_to_alerts
from reliabilityml.detection.evaluate import evaluate
from reliabilityml.features.windows import minute_data, z_matrix, z_scores
from reliabilityml.generator.simulate import default_train, simulate


def test_known_faults_are_detected_quickly():
    tel = simulate(
        replace(default_train(), days=4, seed=21, categories=["dependency_failure", "deployment_regression"])
    )
    z = z_matrix(z_scores(minute_data(tel)))
    report = evaluate(EwmaDetector(threshold=10, consecutive=3).detect(z), tel.faults, 4)
    assert report.recall == 1.0
    assert report.median_ttd_min <= 5


def test_alert_attribution_tracks_the_peak_contributor():
    index = pd.date_range("2026-01-01", periods=7, freq="min")
    score = pd.Series([0.0, 11.0, 12.0, 18.0, 0.0, 0.0, 0.0], index=index)
    culprit = pd.Series(
        [
            "error_rate|api-gateway",
            "error_rate|api-gateway",
            "error_rate|api-gateway",
            "latency_p95|orders-service",
            "error_rate|api-gateway",
            "error_rate|api-gateway",
            "error_rate|api-gateway",
        ],
        index=index,
    )

    alerts = runs_to_alerts(score, threshold=10, consecutive=2, culprit=culprit, cooldown=2)

    assert len(alerts) == 1
    assert alerts[0].fired_at == index[2]
    assert (alerts[0].signal, alerts[0].service, alerts[0].peak) == (
        "latency_p95",
        "orders-service",
        18.0,
    )

    score.iloc[1:4] = [17.0, 12.0, 11.0]
    culprit.iloc[1] = "cpu_utilization|payments-service"
    alerts = runs_to_alerts(score, threshold=10, consecutive=2, culprit=culprit, cooldown=2)

    assert alerts[0].fired_at == index[2]
    assert (alerts[0].signal, alerts[0].service, alerts[0].peak) == (
        "cpu_utilization",
        "payments-service",
        17.0,
    )


def test_predictions_are_deterministic(artifacts):
    model, _ = Registry(artifacts / "registry").load("production")
    x = pd.DataFrame([dict.fromkeys(model.features, 0.1)])
    assert model.predict(x)[0] == model.predict(x)[0]
    assert (model.proba(x) == model.proba(x)).all()


def test_rules_baseline_follows_runbooks():
    base = {
        "db_saturation_ratio": 0.3,
        "edge_traffic_delta": 0.0,
        "services_with_errors": 0,
        "min_since_deploy_error_origin": 240,
        "min_since_any_deploy": 240,
        "memory_slope_60m": 0.0,
    }
    assert rule_for({**base, "db_saturation_ratio": 0.95})[0] == "database_saturation"
    assert rule_for({**base, "edge_traffic_delta": 1.2})[0] == "traffic_spike"
    assert (
        rule_for({**base, "services_with_errors": 1, "min_since_deploy_error_origin": 5})[0] == "deployment_regression"
    )
    assert rule_for({**base, "services_with_errors": 3})[0] == "dependency_failure"
    assert rule_for(base)[0] == "unknown"


def test_pipeline_results_are_honest(artifacts):
    import json

    summary = json.loads((artifacts / "summary.json").read_text())
    cls = summary["classifier"]
    assert 0 <= cls["rules_macro_f1_windows"] <= 1 and 0 <= cls["test_macro_f1_windows"] <= 1
    assert cls["held_out_share_unknown"] is not None
    assert summary["detection"]["recall"] > 0.8
