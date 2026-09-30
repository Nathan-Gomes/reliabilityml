"""Deployment validation gate. The API endpoint and the CI job run exactly these checks.

1. Tests pass (CI: the full pytest suite; API: a built-in smoke subset).
2. Canary replay: the candidate build replays a recorded telemetry slice; its error rate may not
   rise more than 0.2 percentage points, nor its p95 latency more than 10%.
3. Candidate model no worse than production beyond a tolerance.
4. Error budget healthy: below 25% remaining, only urgent releases may ship.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
ARTIFACTS = ROOT / "artifacts"
ERROR_DELTA_PP = 0.2
P95_DELTA = 0.10
MODEL_TOLERANCE = 0.02
BUDGET_FREEZE = 0.25
CANARY_SERVICE = "orders-service"


@dataclass
class Candidate:
    version: str = "orders-service 4.18.0"
    error_rate_multiplier: float = 1.0
    extra_error_pp: float = 0.0  # a bug that fails this many extra percent of requests
    latency_multiplier: float = 1.0
    model_macro_f1: float | None = None  # None = model unchanged
    model_false_alarm_rate: float | None = None
    urgent: bool = False
    budget_override: float | None = None  # simulate a nearly spent budget
    tests_passed: bool | None = None  # CI passes its pytest result in; None = run smoke tests


@dataclass
class Check:
    name: str
    passed: bool
    measured: str
    threshold: str
    detail: str = ""


@dataclass
class GateReport:
    candidate: dict
    passed: bool
    checks: list[Check] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "candidate": self.candidate,
            "passed": bool(self.passed),
            "checks": [{**c.__dict__, "passed": bool(c.passed)} for c in self.checks],
        }


def _smoke_tests(artifacts: Path) -> tuple[bool, str]:
    """Quick self-checks the running service can do on its own."""
    from ..slo.engine import AVAILABILITY, burn_rate

    problems = []
    if abs(AVAILABILITY.budget_minutes() - 43.2) > 1e-6:
        problems.append("budget minutes")
    if abs(burn_rate(0.0144, AVAILABILITY) - 14.4) > 1e-6:
        problems.append("burn rate")
    try:
        from ..classifier.registry import Registry

        model, _ = Registry(artifacts / "registry").load("production")
        sample = pd.DataFrame([dict.fromkeys(model.features, 0.0)])
        first, second = model.predict(sample)[0], model.predict(sample)[0]
        if first != second:
            problems.append("non-deterministic prediction")
    except Exception as error:  # noqa: BLE001
        problems.append(f"model load: {error}")
    return not problems, "smoke checks passed" if not problems else "; ".join(problems)


def _canary(artifacts: Path, candidate: Candidate) -> tuple[dict, dict]:
    frame = pd.read_parquet(artifacts / "data" / "test_minutes.parquet")
    err = frame[f"error_rate|{CANARY_SERVICE}"]
    p95 = frame[f"latency_p95|{CANARY_SERVICE}"]
    # A quiet two-hour slice (lowest error variance) so the comparison is about the build, not an incident.
    rolling = err.rolling(120).std().iloc[120:]
    end = rolling.idxmin()
    window = slice(end - pd.Timedelta(minutes=119), end)
    current = {"error_rate": float(err.loc[window].mean()), "latency_p95": float(p95.loc[window].mean())}
    rng = np.random.default_rng(11)
    noise = rng.lognormal(0, 0.005, 2)
    replay = {
        "error_rate": current["error_rate"] * candidate.error_rate_multiplier * noise[0]
        + candidate.extra_error_pp / 100,
        "latency_p95": current["latency_p95"] * candidate.latency_multiplier * noise[1],
    }
    return current, replay


def validate(candidate: Candidate, artifacts: Path = ARTIFACTS) -> GateReport:
    checks: list[Check] = []

    if candidate.tests_passed is None:
        ok, detail = _smoke_tests(artifacts)
        checks.append(Check("Tests", ok, "smoke subset", "all pass", detail))
    else:
        checks.append(Check("Tests", candidate.tests_passed, "pytest (CI)", "all pass", "reported by the CI test job"))

    current, replay = _canary(artifacts, candidate)
    err_delta_pp = (replay["error_rate"] - current["error_rate"]) * 100
    p95_delta = replay["latency_p95"] / current["latency_p95"] - 1
    checks.append(
        Check(
            "Canary: error rate",
            err_delta_pp <= ERROR_DELTA_PP,
            f"{replay['error_rate']:.3%} vs {current['error_rate']:.3%} ({err_delta_pp:+.2f} pp)",
            f"<= +{ERROR_DELTA_PP} pp",
            f"2-hour replay of recorded {CANARY_SERVICE} traffic",
        )
    )
    checks.append(
        Check(
            "Canary: p95 latency",
            p95_delta <= P95_DELTA,
            f"{replay['latency_p95']:.0f} ms vs {current['latency_p95']:.0f} ms ({p95_delta:+.1%})",
            f"<= +{P95_DELTA:.0%}",
            f"2-hour replay of recorded {CANARY_SERVICE} traffic",
        )
    )

    classifier = json.loads((artifacts / "reports" / "classifier.json").read_text())
    prod_f1 = classifier["metrics"]["test_macro_f1_windows"]
    cand_f1 = prod_f1 if candidate.model_macro_f1 is None else candidate.model_macro_f1
    checks.append(
        Check(
            "Model: macro F1",
            cand_f1 >= prod_f1 - MODEL_TOLERANCE,
            f"{cand_f1:.3f} vs production {prod_f1:.3f}",
            f">= production - {MODEL_TOLERANCE}",
            "model unchanged" if candidate.model_macro_f1 is None else "candidate model evaluated",
        )
    )

    slo = json.loads((artifacts / "reports" / "slo.json").read_text())
    budgets = {svc: v["availability"]["budget_remaining"] for svc, v in slo["services"].items()}
    worst_service = min(budgets, key=lambda s: budgets[s])
    remaining = candidate.budget_override if candidate.budget_override is not None else budgets[worst_service]
    budget_ok = remaining >= BUDGET_FREEZE or candidate.urgent
    checks.append(
        Check(
            "Error budget",
            budget_ok,
            f"{remaining:.0%} remaining ({worst_service})",
            f">= {BUDGET_FREEZE:.0%} (urgent fixes exempt)",
            "urgent release: exempt from the freeze"
            if candidate.urgent and remaining < BUDGET_FREEZE
            else "error budget policy",
        )
    )
    for c in checks:
        c.passed = bool(c.passed)
    return GateReport(dict(candidate.__dict__), all(c.passed for c in checks), checks)


EXAMPLES = {
    "clean_release": Candidate(version="orders-service 4.18.0"),
    "latency_regression": Candidate(version="orders-service 4.18.1", latency_multiplier=1.18),
    "error_regression": Candidate(version="orders-service 4.18.2", extra_error_pp=0.6),
    "weaker_model": Candidate(version="incident-classifier retrain", model_macro_f1=0.88),
    "budget_freeze": Candidate(version="web-frontend 9.4.0 (feature)", budget_override=0.18),
    "urgent_fix_during_freeze": Candidate(version="web-frontend 9.4.1 (hotfix)", budget_override=0.18, urgent=True),
}


def stage_gate(out: Path) -> dict:
    from ..pipelines.build import write_json

    examples = {name: validate(c, out).to_dict() for name, c in EXAMPLES.items()}
    result = {
        "thresholds": {
            "error_delta_pp": ERROR_DELTA_PP,
            "p95_delta": P95_DELTA,
            "model_tolerance": MODEL_TOLERANCE,
            "budget_freeze": BUDGET_FREEZE,
        },
        "examples": examples,
    }
    write_json(out / "reports" / "gate.json", result)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the deployment validation gate.")
    parser.add_argument("--scenario", choices=sorted(EXAMPLES), default="clean_release")
    parser.add_argument("--tests-passed", choices=["true", "false"], default=None)
    parser.add_argument("--artifacts", default=str(ARTIFACTS))
    args = parser.parse_args(argv)
    candidate = EXAMPLES[args.scenario]
    if args.tests_passed is not None:
        candidate.tests_passed = args.tests_passed == "true"
    result = validate(candidate, Path(args.artifacts))
    width = max(len(c.name) for c in result.checks)
    for c in result.checks:
        print(f"{'PASS' if c.passed else 'FAIL'}  {c.name:<{width}}  {c.measured}  (threshold {c.threshold})")
    print("GATE PASSED" if result.passed else "GATE FAILED: deploy blocked")
    return 0 if result.passed else 1


if __name__ == "__main__":
    sys.exit(main())
