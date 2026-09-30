from dataclasses import replace

import numpy as np
import pandas as pd

from reliabilityml.classifier.features import window_features
from reliabilityml.features.windows import ewma_z, minute_data
from reliabilityml.generator.simulate import default_test, default_train, simulate

SMALL = replace(default_train(), days=3)


def test_same_seed_same_output():
    a, b = simulate(SMALL), simulate(SMALL)
    pd.testing.assert_frame_equal(a.metrics, b.metrics)
    pd.testing.assert_frame_equal(a.faults, b.faults)
    assert not simulate(replace(SMALL, seed=8)).metrics.equals(a.metrics)


def test_faults_are_recorded_and_visible():
    tel = simulate(SMALL)
    assert len(tel.faults) >= 3
    assert {"fault_id", "category", "service", "start", "end"} <= set(tel.faults.columns)
    wide = tel.wide("error_rate")
    for f in tel.faults[tel.faults.category == "dependency_failure"].itertuples():
        during = wide.loc[f.start : f.end, f.service].mean()
        before = wide.loc[f.start - pd.Timedelta(hours=1) : f.start - pd.Timedelta(minutes=5), f.service].mean()
        assert during > 10 * before


def test_memory_leak_is_held_out_of_training_but_present_in_test():
    assert "memory_leak" not in simulate(default_train()).faults.category.unique()
    assert (simulate(default_test()).faults.category == "memory_leak").sum() >= 2


def test_most_deploys_are_harmless():
    tel = simulate(default_train())
    regressions = (tel.faults.category == "deployment_regression").sum()
    deploys = (tel.deploys.kind == "deploy").sum()
    assert regressions / deploys < 0.25


def test_ewma_z_at_t_ignores_the_future():
    idx = pd.date_range("2026-01-01", periods=300, freq="min")
    rng = np.random.default_rng(1)
    frame = pd.DataFrame({"svc": 100 + rng.normal(0, 3, 300)}, index=idx)
    changed = frame.copy()
    changed.iloc[200:] += 500  # rewrite the future
    a = ewma_z(frame, "request_rate").iloc[:200]
    b = ewma_z(changed, "request_rate").iloc[:200]
    pd.testing.assert_frame_equal(a, b)


def test_window_features_ignore_the_future():
    tel = simulate(SMALL)
    md = minute_data(tel)
    at = md.index[1500]
    before = window_features(md, at)
    for frame in md.frames.values():
        frame.loc[frame.index > at] = frame.loc[frame.index > at] * 3 + 7
    assert window_features(md, at) == before
