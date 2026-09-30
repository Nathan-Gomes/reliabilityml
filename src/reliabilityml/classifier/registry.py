"""Experiment tracking and model registry.

Every training run is written to a local file registry (always) and to MLflow (when installed and
not disabled with RELIABILITYML_MLFLOW=0). Promotion uses aliases, `staging` and `production`,
which replace the deprecated MLflow stages with the same meaning. The API serves whichever
version holds the `production` alias.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import joblib

MODEL_NAME = "incident-classifier"


def git_sha() -> str:
    env = os.environ.get("GITHUB_SHA") or os.environ.get("RENDER_GIT_COMMIT")
    if env:
        return env[:7]
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], stderr=subprocess.DEVNULL, text=True
        ).strip()
    except Exception:
        return "uncommitted"


def _mlflow():
    if os.environ.get("RELIABILITYML_MLFLOW", "1") == "0":
        return None
    try:
        import mlflow

        return mlflow
    except ImportError:
        return None


@dataclass
class RunRecord:
    run_id: str
    name: str
    params: dict
    metrics: dict
    tags: dict
    version: int | None = None


class Registry:
    def __init__(self, root: Path, tracking_uri: str | None = None, experiment: str = "reliabilityml"):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.mlflow = _mlflow()
        if self.mlflow is not None:
            uri = (
                tracking_uri
                or os.environ.get("MLFLOW_TRACKING_URI")
                or f"sqlite:///{(self.root / 'mlflow.db').resolve()}"
            )
            self.mlflow.set_tracking_uri(uri)
            self.mlflow.set_experiment(experiment)

    # ---------------- runs

    def log_run(
        self, name: str, params: dict, metrics: dict, tags: dict, artifacts: dict[str, Path], model=None
    ) -> RunRecord:
        tags = {"git_sha": git_sha(), **tags}
        run_id = f"local-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"
        if self.mlflow is not None:
            mlflow = self.mlflow
            with mlflow.start_run(run_name=name) as run:
                run_id = run.info.run_id
                mlflow.log_params({k: str(v) for k, v in params.items()})
                mlflow.log_metrics(
                    {re.sub(r"[^\w\-. /]", "_", k): float(v) for k, v in metrics.items() if v is not None}
                )
                mlflow.set_tags(tags)
                for path in artifacts.values():
                    mlflow.log_artifact(str(path))
                if model is not None:
                    tmp = self.root / "tmp-model.joblib"
                    joblib.dump(model, tmp)
                    mlflow.pyfunc.log_model(
                        name="model", python_model=_PyfuncWrapper(), artifacts={"classifier": str(tmp)}
                    )
                    tmp.unlink(missing_ok=True)
        record = RunRecord(run_id, name, params, metrics, tags)
        runs = self._read("runs.json", [])
        runs.append(record.__dict__)
        self._write("runs.json", runs)
        return record

    # ---------------- registry

    def register(self, record: RunRecord, model, alias: str | None = None) -> int:
        versions = self._read("versions.json", [])
        version = len(versions) + 1
        folder = self.root / MODEL_NAME / f"v{version}"
        folder.mkdir(parents=True, exist_ok=True)
        joblib.dump(model, folder / "model.joblib")
        meta = {
            "version": version,
            "run_id": record.run_id,
            "name": record.name,
            "params": record.params,
            "metrics": record.metrics,
            "tags": record.tags,
            "registered_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        (folder / "meta.json").write_text(json.dumps(meta, indent=2))
        versions.append(meta)
        self._write("versions.json", versions)
        if self.mlflow is not None and not record.run_id.startswith("local-"):
            mv = self.mlflow.register_model(f"runs:/{record.run_id}/model", MODEL_NAME)
            meta["mlflow_version"] = int(mv.version)
            versions[-1] = meta
            self._write("versions.json", versions)
        record.version = version
        if alias:
            self.set_alias(alias, version)
        return version

    def set_alias(self, alias: str, version: int) -> None:
        aliases = self._read("aliases.json", {})
        aliases[alias] = version
        self._write("aliases.json", aliases)
        if self.mlflow is not None:
            meta = next((v for v in self._read("versions.json", []) if v["version"] == version), None)
            if meta and meta.get("mlflow_version"):
                self.mlflow.MlflowClient().set_registered_model_alias(MODEL_NAME, alias, str(meta["mlflow_version"]))

    def alias(self, alias: str) -> int | None:
        return self._read("aliases.json", {}).get(alias)

    def load(self, alias: str = "production"):
        version = self.alias(alias)
        if version is None:
            raise LookupError(f"No model holds the '{alias}' alias.")
        folder = self.root / MODEL_NAME / f"v{version}"
        return joblib.load(folder / "model.joblib"), json.loads((folder / "meta.json").read_text())

    def versions(self) -> list[dict]:
        return self._read("versions.json", [])

    # ---------------- helpers

    def _read(self, name: str, default):
        path = self.root / name
        return json.loads(path.read_text()) if path.exists() else default

    def _write(self, name: str, value) -> None:
        (self.root / name).write_text(json.dumps(value, indent=2, default=str))


class _PyfuncWrapper:  # defined lazily so mlflow stays optional
    def __new__(cls):
        import mlflow.pyfunc

        class Wrapper(mlflow.pyfunc.PythonModel):
            def load_context(self, context):
                self.clf = joblib.load(context.artifacts["classifier"])

            def predict(self, context, model_input, params=None):
                labels, _ = self.clf.predict(model_input)
                return labels

        return Wrapper()
