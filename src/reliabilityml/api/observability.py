"""Prometheus metrics: the API's own reliability, plus a replay of the production timeline.

The replay walks the 30-day production timeline at REPLAY_SECONDS_PER_MINUTE real seconds per
simulated minute, so Grafana dashboards show live-looking service metrics, burn rates, alerts and
model health from a docker-compose stack with no real traffic.
"""

from __future__ import annotations

import contextlib
import os
import time

import numpy as np
from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest

from ..slo import engine as slo

REGISTRY = CollectorRegistry()
HTTP_REQUESTS = Counter("rml_http_requests_total", "API requests", ["route", "method", "status"], registry=REGISTRY)
HTTP_LATENCY = Histogram(
    "rml_http_request_seconds",
    "API request latency",
    ["route"],
    registry=REGISTRY,
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5),
)
PREDICTIONS = Counter("rml_predictions_total", "Classifier predictions served", ["label"], registry=REGISTRY)
CONFIDENCE = Histogram(
    "rml_prediction_confidence",
    "Top-class probability of served predictions",
    registry=REGISTRY,
    buckets=(0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 1.0),
)
ASK_TOTAL = Counter("rml_assistant_answers_total", "Assistant answers", ["outcome", "engine"], registry=REGISTRY)
GATE_TOTAL = Counter("rml_gate_runs_total", "Deployment gate runs", ["result"], registry=REGISTRY)

SERVICE_GAUGES = {
    name: Gauge(f"rml_service_{name}", help_text, ["service"], registry=REGISTRY)
    for name, help_text in {
        "request_rate": "Replayed requests per second",
        "latency_p50_ms": "Replayed p50 latency (ms)",
        "latency_p95_ms": "Replayed p95 latency (ms)",
        "latency_p99_ms": "Replayed p99 latency (ms)",
        "error_rate": "Replayed fraction of 5xx responses",
        "cpu_utilization": "Replayed CPU utilization",
        "memory_utilization": "Replayed memory utilization",
    }.items()
}
DB_CONNECTIONS = Gauge("rml_db_connections_in_use", "Replayed postgres-db connections", registry=REGISTRY)
BURN = Gauge("rml_slo_burn_rate", "Availability burn rate", ["service", "window"], registry=REGISTRY)
BUDGET = Gauge(
    "rml_slo_budget_remaining", "Availability error budget remaining (30 days)", ["service"], registry=REGISTRY
)
SLI = Gauge("rml_slo_availability_sli", "Availability SLI over the replay window", ["service"], registry=REGISTRY)
ALERT_ACTIVE = Gauge("rml_burn_alert_active", "Burn-rate alert firing", ["service", "rule"], registry=REGISTRY)
REPLAY_TS = Gauge("rml_replay_timestamp_seconds", "Simulated time of the replay", registry=REGISTRY)
MODEL_INFO = Gauge("rml_model_info", "Production model", ["version", "kind"], registry=REGISTRY)
DRIFT_PSI = Gauge("rml_drift_psi_max", "Largest PSI across monitored signals", registry=REGISTRY)
UNKNOWN_SHARE = Gauge("rml_unknown_share", "Share of test-week alerts routed to unknown", registry=REGISTRY)

SECONDS_PER_MINUTE = float(os.environ.get("REPLAY_SECONDS_PER_MINUTE", "5"))


class Replay:
    def __init__(self, state):
        self.state = state
        self.started = time.time()
        self._sli = None

    def _prepare(self):
        m = self.state.ops_minutes
        self.index = m["error_rate"].index
        self.services = [s for s in m["error_rate"].columns if s != "postgres-db"]
        self._sli = {
            svc: slo.sli_minutes(
                m["request_rate"][svc], m["error_rate"][svc], m["latency_p50"][svc], m["latency_p95"][svc]
            )
            for svc in self.services
        }
        self.burn = {
            svc: {
                w: slo.rolling_burn(self._sli[svc], "bad_availability", w, slo.AVAILABILITY).to_numpy()
                for w in ("5min", "1h", "6h")
            }
            for svc in self.services
        }
        self.cum_bad = {s: self._sli[s]["bad_availability"].cumsum().to_numpy() for s in self.services}
        self.cum_total = {s: self._sli[s]["total"].cumsum().to_numpy() for s in self.services}

    def update(self) -> None:
        if self._sli is None:
            self._prepare()
        m = self.state.ops_minutes
        # Start a day in, so the 1-hour and 6-hour windows are already full.
        i = (1440 + int((time.time() - self.started) / SECONDS_PER_MINUTE)) % len(self.index)
        REPLAY_TS.set(self.index[i].timestamp())
        names = {
            "request_rate": "request_rate",
            "latency_p50": "latency_p50_ms",
            "latency_p95": "latency_p95_ms",
            "latency_p99": "latency_p99_ms",
            "error_rate": "error_rate",
            "cpu_utilization": "cpu_utilization",
            "memory_utilization": "memory_utilization",
        }
        for signal, gauge in names.items():
            row = m[signal].iloc[i]
            for svc, value in row.items():
                if np.isfinite(value):
                    SERVICE_GAUGES[gauge].labels(svc).set(float(value))
        DB_CONNECTIONS.set(float(m["db_connections_in_use"]["postgres-db"].iloc[i]))
        start = max(0, i - 30 * 1440)
        for svc in self.services:
            for window, values in self.burn[svc].items():
                value = values[i]
                BURN.labels(svc, window).set(float(value) if np.isfinite(value) else 0.0)
            bad = self.cum_bad[svc][i] - (self.cum_bad[svc][start - 1] if start else 0)
            total = self.cum_total[svc][i] - (self.cum_total[svc][start - 1] if start else 0)
            frac = bad / total if total else 0.0
            SLI.labels(svc).set(1 - frac)
            BUDGET.labels(svc).set(1 - frac / slo.AVAILABILITY.allowed_error_rate)
            fast = self.burn[svc]["1h"][i] > 14.4 and self.burn[svc]["5min"][i] > 14.4
            ALERT_ACTIVE.labels(svc, "1h/5min > 14.4").set(1.0 if fast else 0.0)


def static_metrics(state) -> None:
    try:
        _, meta = state.model
        MODEL_INFO.labels(str(meta["version"]), meta["params"].get("model_type", "")).set(1)
        DRIFT_PSI.set(float(state.report("drift")["max_psi"]))
        preds = [i["predicted"] for i in state.incidents]
        UNKNOWN_SHARE.set(float(np.mean([p == "unknown" for p in preds])) if preds else 0.0)
    except Exception:  # noqa: BLE001 - metrics must never break the API
        pass


def render(replay: Replay) -> bytes:
    with contextlib.suppress(Exception):
        replay.update()
    return generate_latest(REGISTRY)


def setup_tracing(app) -> bool:
    """Instrument FastAPI with OpenTelemetry when an OTLP endpoint is configured."""
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not endpoint:
        return False
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        return False
    provider = TracerProvider(resource=Resource.create({"service.name": "reliabilityml-api"}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app, excluded_urls="metrics,health")
    return True
