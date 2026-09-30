"""Everything the API serves, loaded once at start-up from artifacts/ (written by the pipeline)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

import pandas as pd

from ..classifier.registry import Registry
from ..generator.topology import CALLS, SERVICE_NAMES
from ..rag.assistant import Assistant
from ..rag.corpus import chunk_documents, load_documents
from ..rag.index import VectorStore, make_embedder

ARTIFACTS = Path(os.environ.get("RELIABILITYML_ARTIFACTS", Path(__file__).resolve().parents[3] / "artifacts"))


def _json(path: Path):
    return json.loads(path.read_text())


def _wide(path: Path) -> dict[str, pd.DataFrame]:
    frame = pd.read_parquet(path)
    out: dict[str, pd.DataFrame] = {}
    for col in frame.columns:
        signal, service = col.split("|")
        out.setdefault(signal, pd.DataFrame(index=frame.index))[service] = frame[col]
    return out


@dataclass
class AppState:
    root: Path = ARTIFACTS

    @cached_property
    def summary(self) -> dict:
        return _json(self.root / "summary.json")

    def report(self, name: str) -> dict:
        return _json(self.root / "reports" / f"{name}.json")

    @cached_property
    def incidents(self) -> list[dict]:
        return _json(self.root / "incidents.json")

    @cached_property
    def test_minutes(self) -> dict[str, pd.DataFrame]:
        return _wide(self.root / "data" / "test_minutes.parquet")

    @cached_property
    def ops_minutes(self) -> dict[str, pd.DataFrame]:
        return _wide(self.root / "data" / "ops_minutes.parquet")

    @cached_property
    def logs(self) -> pd.DataFrame:
        return pd.read_csv(self.root / "data" / "test_logs.csv", parse_dates=["ts"])

    @cached_property
    def deploys(self) -> pd.DataFrame:
        return pd.read_csv(self.root / "data" / "test_deploys.csv", parse_dates=["ts"])

    @cached_property
    def registry(self) -> Registry:
        return Registry(self.root / "registry")

    @cached_property
    def model(self):
        return self.registry.load("production")

    @cached_property
    def assistant(self) -> Assistant:
        cfg = self.report("rag")["config"]
        store = VectorStore(make_embedder(cfg["embedder"]), chunk_documents(load_documents()))
        return Assistant(
            store,
            k=cfg["k"],
            min_score=cfg["min_score"],
            min_overlap=cfg["min_overlap"],
            min_coverage=cfg.get("min_coverage", 0.5),
        )

    def incident(self, incident_id: str) -> dict | None:
        return next((i for i in self.incidents if i["id"] == incident_id), None)

    def incident_detail(self, incident: dict) -> dict:
        fired = pd.Timestamp(incident["fired_at"])
        window = slice(fired - pd.Timedelta(minutes=60), fired + pd.Timedelta(minutes=45))
        series = {}
        for signal in ("error_rate", "latency_p99", "request_rate", "memory_utilization", "db_connections_in_use"):
            frame = self.test_minutes[signal].loc[window]
            series[signal] = {
                "ts": [t.isoformat() for t in frame.index],
                **{svc: frame[svc].round(5).tolist() for svc in frame.columns if frame[svc].notna().any()},
            }
        logs = self.logs[
            (self.logs["ts"] >= fired - pd.Timedelta(minutes=30))
            & (self.logs["ts"] <= fired + pd.Timedelta(minutes=30))
        ]
        deploys = self.deploys[(self.deploys["ts"] >= fired - pd.Timedelta(hours=2)) & (self.deploys["ts"] <= fired)]
        return {
            **incident,
            "series": series,
            "trace": self.trace(fired),
            "logs": logs.tail(12).assign(ts=lambda d: d["ts"].astype(str)).to_dict("records"),
            "deploys": deploys.assign(ts=lambda d: d["ts"].astype(str)).to_dict("records"),
        }

    def trace(self, at: pd.Timestamp) -> dict:
        """An OpenTelemetry-shaped sample trace at the alert minute, built from per-service span times."""
        span = self.test_minutes["span_ms"]
        now = span.loc[at - pd.Timedelta(minutes=4) : at].mean()
        base = span.loc[at - pd.Timedelta(minutes=70) : at - pd.Timedelta(minutes=10)].median()
        counter = iter(range(1, 100))

        def build(service: str, parent: str | None, start: float) -> dict:
            span_id = f"{next(counter):016x}"
            own = float(now[service])
            children, cursor = [], start + own * 0.3
            for callee, share, _calls in CALLS[service]:
                if share >= 0.5 or service == "api-gateway":
                    child = build(callee, span_id, cursor)
                    children.append(child)
                    cursor = child["end_ms"]
            end = max(cursor, start + own) + own * 0.2
            return {
                "span_id": span_id,
                "parent_span_id": parent,
                "name": f"{service} handle",
                "service": service,
                "start_ms": round(start, 1),
                "end_ms": round(end, 1),
                "self_ms": round(own, 1),
                "self_delta_ms": round(own - float(base[service]), 1),
                "children": children,
            }

        root = build("web-frontend", None, 0.0)
        flat = []

        def walk(node):
            flat.append(node)
            for child in node["children"]:
                walk(child)

        walk(root)
        top = max(flat, key=lambda n: n["self_delta_ms"])
        return {
            "trace_id": f"{abs(hash(str(at))) % (1 << 64):016x}",
            "root": root,
            "slowest_added": top["service"],
            "services": SERVICE_NAMES,
        }
