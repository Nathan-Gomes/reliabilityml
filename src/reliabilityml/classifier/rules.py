"""Hand-written rules baseline: the honest control for the ML model.

The rules come straight from the runbooks. They know about memory leaks (a person wrote them
from experience), which the model has never seen, so the comparison is deliberately fair to them.
"""

from __future__ import annotations

import pandas as pd

UNKNOWN = "unknown"


def rule_for(f: pd.Series | dict) -> tuple[str, str]:
    """Return (category, the rule that fired)."""
    if f["db_saturation_ratio"] >= 0.85 and f["edge_traffic_delta"] < 0.5:
        return "database_saturation", "DB connections >= 85% of pool and traffic not elevated"
    if f["edge_traffic_delta"] >= 0.5:
        return "traffic_spike", "edge request rate >= 50% above baseline"
    if f["services_with_errors"] >= 1 and f["min_since_deploy_error_origin"] <= 15:
        return "deployment_regression", "errors up on a service deployed in the last 15 minutes"
    if f["services_with_errors"] >= 2:
        return "dependency_failure", "errors up on two or more services in the call chain"
    if f["memory_slope_60m"] >= 0.12:
        return "memory_leak", "memory up >= 12 points in the last hour"
    if f["services_with_errors"] >= 1 and f["min_since_any_deploy"] <= 15:
        return "deployment_regression", "errors up and a deploy anywhere in the last 15 minutes"
    return UNKNOWN, "no rule matched"


def predict(features: pd.DataFrame) -> list[str]:
    return [rule_for(row)[0] for _, row in features.iterrows()]
