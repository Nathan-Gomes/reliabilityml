"""FastAPI service.

Spec endpoints live at the root (/health, /metrics, /incidents, /incidents/{id}, /predict, /slo/{service},
/ask, /validate-deployment). Dashboard data lives under /api. The compiled React app is served for
every other path.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Literal

import pandas as pd
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ..classifier import rules
from ..classifier.features import feature_names, neutral
from ..classifier.registry import git_sha
from ..gate.validate import Candidate, validate
from . import observability as obs
from .state import AppState

STATIC = Path(__file__).resolve().parent.parent / "static"
state = AppState()
app = FastAPI(
    title="ReliabilityML API",
    version="1.0.0",
    description="Incident detection, classification, SLOs and a cited troubleshooting assistant, on synthetic telemetry.",
)
replay = obs.Replay(state)
TRACING = obs.setup_tracing(app)


@app.middleware("http")
async def measure(request: Request, call_next):
    started = time.perf_counter()
    response = await call_next(request)
    route = request.scope.get("route")
    path = getattr(route, "path", "static")
    if path != "/metrics":
        obs.HTTP_REQUESTS.labels(path, request.method, str(response.status_code)).inc()
        obs.HTTP_LATENCY.labels(path).observe(time.perf_counter() - started)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    return response


@app.on_event("startup")
def warm() -> None:
    from ..cloud import download

    download(state.root)  # no-op unless RELIABILITYML_ARTIFACTS_BUCKET is set
    _ = state.model, state.incidents, state.assistant
    obs.static_metrics(state)


# ------------------------------------------------------------------ spec endpoints


@app.get("/health")
def health() -> dict:
    _, meta = state.model
    return {
        "status": "ok",
        "commit": os.environ.get("RENDER_GIT_COMMIT", git_sha())[:7],
        "model_version": meta["version"],
        "data": "synthetic",
        "tracing": TRACING,
        "assistant_engine": "claude" if state.assistant.api_key else "extractive",
    }


@app.get("/metrics", include_in_schema=False)
def metrics() -> Response:
    return Response(obs.render(replay), media_type="text/plain; version=0.0.4")


def _incident_summary(i: dict) -> dict:
    keys = (
        "id",
        "fired_at",
        "ended_at",
        "service",
        "signal",
        "peak_z",
        "predicted",
        "confidence",
        "rules_prediction",
        "status",
    )
    truth = i.get("ground_truth")
    return {
        **{k: i[k] for k in keys},
        "actual": truth["category"] if truth else "false alert",
        "unknown_reason": i["explanation"].get("unknown_reason"),
    }


@app.get("/incidents")
@app.get("/api/incidents")
def incidents(category: str | None = None) -> list[dict]:
    rows = [_incident_summary(i) for i in state.incidents]
    return [r for r in rows if category is None or r["predicted"] == category]


@app.get("/incidents/{incident_id}")
@app.get("/api/incidents/{incident_id}")
def incident(incident_id: str) -> dict:
    found = state.incident(incident_id)
    if found is None:
        raise HTTPException(404, f"Incident {incident_id} not found.")
    return state.incident_detail(found)


class PredictRequest(BaseModel):
    features: dict[str, float] = Field(
        default_factory=dict, description="Feature window; missing features take neutral values."
    )
    incident_id: str | None = None


@app.post("/predict")
def predict(body: PredictRequest) -> dict:
    model, meta = state.model
    if body.incident_id:
        found = state.incident(body.incident_id)
        if found is None:
            raise HTTPException(404, f"Incident {body.incident_id} not found.")
        from ..classifier.features import frame_for
        from ..features.windows import MinuteData

        md = MinuteData(state.test_minutes, state.deploys.assign(minute=state.deploys["ts"].dt.floor("min")))
        x = frame_for(md, [pd.Timestamp(found["fired_at"])], [found["service"]])
    else:
        unknown = sorted(set(body.features) - set(feature_names()))
        if unknown:
            raise HTTPException(422, f"Unknown features: {', '.join(unknown[:5])}")
        x = pd.DataFrame([{name: body.features.get(name, neutral(name)) for name in feature_names()}])
    labels, conf = model.predict(x)
    explanation = model.explain(x)[0]
    rule, reason = rules.rule_for(x.iloc[0])
    obs.PREDICTIONS.labels(labels[0]).inc()
    obs.CONFIDENCE.observe(float(conf[0]))
    return {
        "prediction": labels[0],
        "confidence": float(conf[0]),
        **explanation,
        "rules_baseline": {"prediction": rule, "rule": reason},
        "model_version": meta["version"],
        "features": x.iloc[0].round(4).to_dict(),
    }


@app.get("/slo/{service}")
@app.get("/api/slo/{service}")
def slo_status(service: str) -> dict:
    report = state.report("slo")
    if service not in report["services"]:
        raise HTTPException(404, f"No SLO for '{service}'.")
    svc = report["services"][service]
    return {
        "service": service,
        "availability": svc["availability"],
        "latency": svc["latency"],
        "alerts": [a for a in report["alerts"] if a["service"] == service],
        "window": report["window"],
        "series": svc["series"],
    }


class AskRequest(BaseModel):
    question: str = Field(min_length=5, max_length=500)
    incident_id: str | None = None
    engine: Literal["auto", "extractive", "claude"] = "auto"


@app.post("/ask")
@app.post("/api/ask")
def ask(body: AskRequest) -> dict:
    incident = state.incident(body.incident_id) if body.incident_id else None
    if body.incident_id and incident is None:
        raise HTTPException(404, f"Incident {body.incident_id} not found.")
    answer = state.assistant.ask(body.question, incident, engine=body.engine)
    obs.ASK_TOTAL.labels("refused" if answer.refused else "answered", answer.engine).inc()
    return answer.to_dict()


class GateRequest(BaseModel):
    version: str = Field("orders-service 4.18.0", max_length=80)
    error_rate_multiplier: float = Field(1.0, ge=0, le=100)
    extra_error_pp: float = Field(0.0, ge=0, le=50)
    latency_multiplier: float = Field(1.0, ge=0.1, le=10)
    model_macro_f1: float | None = Field(None, ge=0, le=1)
    urgent: bool = False
    budget_override: float | None = Field(None, ge=-5, le=1)


@app.post("/validate-deployment")
@app.post("/api/validate-deployment")
def validate_deployment(body: GateRequest) -> dict:
    result = validate(Candidate(**body.model_dump()), state.root).to_dict()
    obs.GATE_TOTAL.labels("pass" if result["passed"] else "fail").inc()
    return result


# ------------------------------------------------------------------ dashboard data


@app.get("/api/summary")
def summary() -> dict:
    _, meta = state.model
    return {
        **state.summary,
        "model": {
            "version": meta["version"],
            "name": meta["name"],
            "registered_at": meta["registered_at"],
            "run_id": meta["run_id"],
        },
    }


@app.get("/api/report/{name}")
def report(name: Literal["detection", "classifier", "slo", "rag", "drift", "gate"]) -> dict:
    data = state.report(name)
    if name == "rag":
        data = {k: v for k, v in data.items() if k != "tuning"}
    return data


@app.get("/api/registry")
def registry() -> dict:
    reg = state.registry
    return {"aliases": {a: reg.alias(a) for a in ("production", "staging")}, "versions": reg.versions()}


@app.get("/api/services")
def services(hours: int = 24) -> dict:
    """Service overview from the production replay: the last `hours` hours at 5-minute resolution."""
    m = state.ops_minutes
    end = m["error_rate"].index[-1]
    out = {}
    for signal in ("request_rate", "latency_p95", "latency_p99", "error_rate", "cpu_utilization", "memory_utilization"):
        frame = m[signal].loc[end - pd.Timedelta(hours=min(hours, 168)) :].resample("5min").mean()
        out[signal] = {
            "ts": [t.isoformat() for t in frame.index],
            **{svc: frame[svc].round(5).tolist() for svc in frame.columns if frame[svc].notna().any()},
        }
    return out


# ------------------------------------------------------------------ front end

if (STATIC / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=STATIC / "assets"), name="assets")


@app.get("/{path:path}", include_in_schema=False)
def spa(path: str):
    if path.startswith("api/"):
        return JSONResponse({"detail": "Not found"}, status_code=404)
    candidate = (STATIC / path).resolve()
    if path and candidate.is_file() and STATIC.resolve() in candidate.parents:
        return FileResponse(candidate)
    index = STATIC / "index.html"
    if index.is_file():
        return FileResponse(index)
    return JSONResponse({"detail": "Front end not built; see README."}, status_code=503)
