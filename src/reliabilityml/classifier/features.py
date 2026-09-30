"""Features for one incident window, computed only from data up to the moment the alert fired.

Window  W = the 5 minutes ending at the alert minute (inclusive).
Baseline B = minutes t-70 .. t-10, so the start of an incident does not leak into its own baseline.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..features.windows import MinuteData
from ..generator.topology import DB_POOL_MAX, SERVICE_BY_NAME, SERVICE_NAMES, dependencies_of

FEATURE_VERSION = "fv3"
PER_SERVICE_SIGNALS = [
    "latency_p50",
    "latency_p95",
    "latency_p99",
    "error_rate",
    "cpu_utilization",
    "memory_utilization",
    "request_rate",
]
EPS = {
    "latency_p50": 5.0,
    "latency_p95": 10.0,
    "latency_p99": 15.0,
    "error_rate": 0.002,
    "cpu_utilization": 0.05,
    "memory_utilization": 0.05,
    "request_rate": 5.0,
}
DEPLOY_CAP_MIN = 240.0
ERROR_UP = 0.01  # absolute error-rate increase that counts as "errors up" for a service


# Values meaning "nothing notable" for features a caller leaves out (e.g. hand-built /predict input).
NEUTRAL = {
    "min_since_deploy_error_origin": DEPLOY_CAP_MIN,
    "min_since_deploy_origin_or_deps": DEPLOY_CAP_MIN,
    "min_since_any_deploy": DEPLOY_CAP_MIN,
    "deepest_error_depth": -1.0,
    "shallowest_error_depth": -1.0,
    "culprit_depth": -1.0,
    "db_saturation_ratio": 0.35,
}


def neutral(name: str) -> float:
    return NEUTRAL.get(name, 0.0)


def _short(service: str) -> str:
    return service.split("-")[0]


def feature_names() -> list[str]:
    names = [f"{_short(s)}_{g}_delta" for s in SERVICE_NAMES for g in PER_SERVICE_SIGNALS]
    return names + [
        "db_saturation_ratio",
        "db_connections_delta",
        "services_with_errors",
        "deepest_error_depth",
        "shallowest_error_depth",
        "top_span_depth",
        "error_log_ratio",
        "min_since_deploy_error_origin",
        "min_since_deploy_origin_or_deps",
        "min_since_any_deploy",
        "edge_traffic_delta",
        "memory_slope_60m",
        "culprit_depth",
    ]


def window_features(md: MinuteData, at: pd.Timestamp, culprit_service: str | None = None) -> dict[str, float]:
    at = pd.Timestamp(at).floor("min")
    w = slice(at - pd.Timedelta(minutes=4), at)
    b = slice(at - pd.Timedelta(minutes=70), at - pd.Timedelta(minutes=10))
    f: dict[str, float] = {}
    error_delta: dict[str, float] = {}
    span_delta: dict[str, float] = {}
    for signal in PER_SERVICE_SIGNALS:
        frame = md.signal(signal)
        now = frame.loc[w].mean()
        base = frame.loc[b].median()
        for s in SERVICE_NAMES:
            if signal == "error_rate":
                delta = float(now[s] - base[s])  # errors: absolute change is what matters
                error_delta[s] = delta
                f[f"{_short(s)}_{signal}_delta"] = delta
            else:
                f[f"{_short(s)}_{signal}_delta"] = float((now[s] - base[s]) / (abs(base[s]) + EPS[signal]))
    span = md.signal("span_ms")
    span_now, span_base = span.loc[w].mean(), span.loc[b].median()
    for s in SERVICE_NAMES:
        span_delta[s] = float(span_now[s] - span_base[s])

    conn = md.signal("db_connections_in_use")["postgres-db"]
    f["db_saturation_ratio"] = float(conn.loc[w].max() / DB_POOL_MAX)
    f["db_connections_delta"] = float(conn.loc[w].mean() - conn.loc[b].median())

    erroring = [s for s in SERVICE_NAMES if error_delta[s] > ERROR_UP]
    depths = [SERVICE_BY_NAME[s].depth for s in erroring]
    f["services_with_errors"] = float(len(erroring))
    f["deepest_error_depth"] = float(max(depths)) if depths else -1.0
    f["shallowest_error_depth"] = float(min(depths)) if depths else -1.0
    top_span = max(span_delta, key=lambda s: span_delta[s])
    f["top_span_depth"] = float(SERVICE_BY_NAME[top_span].depth)

    logs = md.signal("log_error").sum(axis=1)
    f["error_log_ratio"] = float(np.log1p(logs.loc[w].mean()) - np.log1p(logs.loc[b].median()))

    deploys = md.deploys[md.deploys["minute"] <= at]

    def minutes_since(services: list[str]) -> float:
        rows = deploys[deploys["service"].isin(services)]
        if rows.empty:
            return DEPLOY_CAP_MIN
        return float(min(DEPLOY_CAP_MIN, (at - rows["minute"].max()).total_seconds() / 60))

    origin = max(erroring, key=lambda s: SERVICE_BY_NAME[s].depth) if erroring else (culprit_service or top_span)
    f["min_since_deploy_error_origin"] = minutes_since([origin]) if erroring else DEPLOY_CAP_MIN
    f["min_since_deploy_origin_or_deps"] = minutes_since([origin, *dependencies_of(origin)])
    f["min_since_any_deploy"] = minutes_since(SERVICE_NAMES)

    f["edge_traffic_delta"] = f["web_request_rate_delta"]
    mem = md.signal("memory_utilization")
    recent = mem.loc[at - pd.Timedelta(minutes=9) : at].mean()
    earlier = mem.loc[at - pd.Timedelta(minutes=70) : at - pd.Timedelta(minutes=60)].mean()
    f["memory_slope_60m"] = float((recent - earlier).max())
    f["culprit_depth"] = float(SERVICE_BY_NAME[culprit_service].depth) if culprit_service else -1.0
    return {k: (0.0 if not np.isfinite(v) else v) for k, v in f.items()}


def frame_for(md: MinuteData, times: list[pd.Timestamp], culprits: list[str | None]) -> pd.DataFrame:
    rows = [window_features(md, t, c) for t, c in zip(times, culprits)]
    return pd.DataFrame(rows, columns=feature_names())
