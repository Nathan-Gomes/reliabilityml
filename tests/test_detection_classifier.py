from dataclasses import replace

import pandas as pd

from reliabilityml.classifier.registry import Registry
from reliabilityml.classifier.rules import rule_for
from reliabilityml.detection.detectors import EwmaDetector
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
