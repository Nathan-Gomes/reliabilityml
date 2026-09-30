"""Generate the four Grafana dashboards from panel definitions (keeps the JSON consistent)."""

import json
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "dashboards" / "grafana" / "json"
DS = {"type": "prometheus", "uid": "prom"}


def panel(pid, title, exprs, x, y, w=12, h=8, unit="short", kind="timeseries", thresholds=None, legend="{{service}}"):
    p = {
        "id": pid,
        "title": title,
        "type": kind,
        "datasource": DS,
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "targets": [
            {"refId": chr(65 + i), "expr": e, "legendFormat": legend, "datasource": DS} for i, e in enumerate(exprs)
        ],
        "fieldConfig": {"defaults": {"unit": unit}, "overrides": []},
        "options": {"legend": {"displayMode": "list", "placement": "bottom"}},
    }
    if thresholds:
        p["fieldConfig"]["defaults"]["thresholds"] = {
            "mode": "absolute",
            "steps": [{"color": c, "value": v} for v, c in thresholds],
        }
        p["fieldConfig"]["defaults"]["custom"] = {"thresholdsStyle": {"mode": "dashed"}}
    return p


def dashboard(uid, title, panels):
    return {
        "uid": uid,
        "title": title,
        "tags": ["reliabilityml"],
        "timezone": "browser",
        "schemaVersion": 39,
        "refresh": "15s",
        "time": {"from": "now-1h", "to": "now"},
        "panels": panels,
    }


boards = {
    "services.json": dashboard(
        "rml-services",
        "Service overview",
        [
            panel(1, "Request rate", ["rml_service_request_rate"], 0, 0, unit="reqps"),
            panel(2, "Error rate", ["rml_service_error_rate"], 12, 0, unit="percentunit"),
            panel(3, "p95 latency", ["rml_service_latency_p95_ms"], 0, 8, unit="ms"),
            panel(4, "p99 latency", ["rml_service_latency_p99_ms"], 12, 8, unit="ms"),
            panel(5, "CPU utilization", ["rml_service_cpu_utilization"], 0, 16, unit="percentunit"),
            panel(
                6,
                "postgres-db connections in use (pool max 100)",
                ["rml_db_connections_in_use"],
                12,
                16,
                thresholds=[(None, "green"), (85, "red")],
                legend="connections",
            ),
        ],
    ),
    "slo.json": dashboard(
        "rml-slo",
        "SLOs and error budgets",
        [
            panel(
                1,
                "Availability SLI (30 days) vs 99.9% target",
                ["rml_slo_availability_sli"],
                0,
                0,
                w=8,
                unit="percentunit",
                kind="stat",
            ),
            panel(
                2,
                "Error budget remaining",
                ["rml_slo_budget_remaining"],
                8,
                0,
                w=8,
                unit="percentunit",
                kind="bargauge",
                thresholds=[(None, "red"), (0.25, "orange"), (0.5, "green")],
            ),
            panel(
                3, "Burn-rate alerts firing", ["sum(rml_burn_alert_active)"], 16, 0, w=8, kind="stat", legend="alerts"
            ),
            panel(
                4,
                "Burn rate, 1-hour window (page above 14.4)",
                ['rml_slo_burn_rate{window="1h"}'],
                0,
                8,
                thresholds=[(None, "green"), (14.4, "red")],
            ),
            panel(
                5,
                "Burn rate, 6-hour window (page above 6)",
                ['rml_slo_burn_rate{window="6h"}'],
                12,
                8,
                thresholds=[(None, "green"), (6, "red")],
            ),
        ],
    ),
    "incidents.json": dashboard(
        "rml-incidents",
        "Incidents",
        [
            panel(1, "Active burn-rate alerts by service", ["rml_burn_alert_active"], 0, 0, w=24),
            panel(
                2,
                "Predictions served by category",
                ["sum by (label) (increase(rml_predictions_total[1h]))"],
                0,
                8,
                kind="bargauge",
                legend="{{label}}",
            ),
            panel(
                3,
                "Assistant answers vs refusals",
                ["sum by (outcome) (increase(rml_assistant_answers_total[1h]))"],
                12,
                8,
                kind="bargauge",
                legend="{{outcome}}",
            ),
        ],
    ),
    "model.json": dashboard(
        "rml-model",
        "Model health",
        [
            panel(1, "Prediction volume", ["sum(rate(rml_predictions_total[5m]))"], 0, 0, legend="predictions/s"),
            panel(
                2,
                "Confidence distribution",
                ["sum by (le) (increase(rml_prediction_confidence_bucket[1h]))"],
                12,
                0,
                kind="bargauge",
                legend="≤ {{le}}",
            ),
            panel(
                3,
                "Largest drift PSI (above 0.25 = significant)",
                ["rml_drift_psi_max"],
                0,
                8,
                w=8,
                kind="stat",
                legend="PSI",
                thresholds=[(None, "green"), (0.1, "orange"), (0.25, "red")],
            ),
            panel(
                4,
                "Share of alerts routed to unknown",
                ["rml_unknown_share"],
                8,
                8,
                w=8,
                kind="stat",
                unit="percentunit",
                legend="unknown",
            ),
            panel(5, "Production model", ["rml_model_info"], 16, 8, w=8, kind="stat", legend="v{{version}} {{kind}}"),
            panel(
                6,
                "API p95 latency by route",
                ["histogram_quantile(0.95, sum by (le, route) (rate(rml_http_request_seconds_bucket[5m])))"],
                0,
                16,
                w=24,
                unit="s",
                legend="{{route}}",
            ),
        ],
    ),
}

if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for name, board in boards.items():
        (OUT / name).write_text(json.dumps(board, indent=2))
        print("wrote", name)
