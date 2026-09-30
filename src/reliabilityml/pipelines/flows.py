"""Prefect flows for the scheduled pipelines. Prefect is optional: without it these run as plain functions.

Deploy the daily drift flow with, for example:
    prefect deploy src/reliabilityml/pipelines/flows.py:daily_drift_check --cron "0 6 * * *"
In the GCP design, Cloud Scheduler triggers the same entry point on Cloud Run jobs.
"""

from __future__ import annotations

from pathlib import Path

try:
    from prefect import flow, task
except ImportError:  # pragma: no cover - optional dependency

    def flow(*args, **kwargs):
        return (lambda fn: fn) if not (args and callable(args[0])) else args[0]

    task = flow

from . import build


@task
def _data(out: Path):
    return build.stage_data(out)


@flow(name="reliabilityml-full-build")
def full_build(out: str = str(build.ARTIFACTS)) -> dict:
    return build.run(Path(out))


@flow(name="reliabilityml-daily-drift-check")
def daily_drift_check(out: str = str(build.ARTIFACTS)) -> dict:
    """Recompute drift against the training distribution and retrain/promote only through the gate."""
    from ..classifier.registry import Registry
    from ..drift.cycle import stage_drift

    path = Path(out)
    registry = Registry(path / "registry")
    ds = _data(path)
    det = build.stage_detection(ds, path)
    cls = build.stage_classifier(ds, det, path, registry)
    return stage_drift(ds, cls, path, registry)


if __name__ == "__main__":
    full_build()
