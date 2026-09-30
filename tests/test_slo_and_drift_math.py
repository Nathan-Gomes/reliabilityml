import numpy as np
import pandas as pd
import pytest

from reliabilityml.drift.cycle import level, psi
from reliabilityml.slo import engine as slo


def test_budget_minutes_for_three_nines_over_30_days():
    assert slo.AVAILABILITY.budget_minutes() == pytest.approx(43.2)


def test_burn_rate_matches_hand_calculation():
    # 1.44% errors against a 0.1% allowance burns 14.4x: the fast page threshold.
    assert slo.burn_rate(0.0144, slo.AVAILABILITY) == pytest.approx(14.4)
    assert slo.burn_rate(0.001, slo.AVAILABILITY) == pytest.approx(1.0)


def test_burn_rules_spend_the_documented_budget_share():
    shares = {(r.long, r.threshold): r.budget_share for r in slo.BURN_RULES}
    assert shares[("1h", 14.4)] == pytest.approx(0.02)
    assert shares[("6h", 6.0)] == pytest.approx(0.05)
    assert shares[("3D", 1.0)] == pytest.approx(0.10)


def test_multiwindow_alert_needs_both_windows():
    idx = pd.date_range("2026-01-01", periods=180, freq="min")
    total = pd.Series(1000.0, index=idx)
    bad = pd.Series(0.0, index=idx)
    bad.iloc[100:103] = 1000 * 0.5  # a 3-minute blip: the 5-minute window spikes, the hour does not
    sli = pd.DataFrame({"total": total, "bad_availability": bad})
    alerts = slo.burn_alerts(sli, "bad_availability", slo.AVAILABILITY)
    assert alerts[alerts["rule"].str.startswith("1h")].shape[0] == 1  # 1.5% over an hour still > 14.4x
    assert alerts[alerts["rule"].str.startswith("3D")].empty  # never fires before 3 days of data exist
    bad.iloc[100:103] = 1000 * 0.05  # 0.25% over the hour: the short window spikes, the long one does not
    quiet = slo.burn_alerts(
        pd.DataFrame({"total": total, "bad_availability": bad}), "bad_availability", slo.AVAILABILITY
    )
    assert (quiet["severity"] == "page").sum() == 0


def test_latency_sli_from_lognormal_fit():
    # median 100 ms, p95 300 ms -> exactly 95% under 300 ms
    assert slo.latency_good_share(np.array([100.0]), np.array([300.0]))[0] == pytest.approx(0.95, abs=1e-6)


def test_psi_on_known_distributions():
    rng = np.random.default_rng(0)
    base = rng.normal(0, 1, 20000)
    assert psi(base, rng.normal(0, 1, 20000)) < 0.02
    assert level(psi(base, rng.normal(0.35, 1, 20000))) == "moderate"
    assert level(psi(base, rng.normal(1.0, 1, 20000))) == "significant"
