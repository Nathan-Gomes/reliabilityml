"""Seeded telemetry simulator with fault injection.

Every signal is produced for every service every 15 seconds. Faults change the underlying
behaviour of one service (the root cause); latency, errors and load then propagate through
the call graph, which is what makes categories overlap:

- database saturation raises upstream latency *and* upstream errors,
- a traffic spike raises latency and database connections, like saturation,
- a dependency failure spreads errors to callers and may happen next to an unrelated deploy,
- a memory leak degrades latency slowly and only shows errors when containers restart.

Short noise spikes and mostly-clean deploys are added so that neither "something moved"
nor "a deploy happened" is a free label.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from .topology import (
    CALLS,
    CATEGORIES,
    DB_POOL_MAX,
    RESOLVE_ORDER,
    SERVICE_BY_NAME,
    SERVICE_NAMES,
    SERVICES,
    STEP_SECONDS,
    STEPS_PER_DAY,
    STEPS_PER_MINUTE,
    callers_of,
)

SIGNALS = [
    "request_rate",
    "latency_p50",
    "latency_p95",
    "latency_p99",
    "error_rate",
    "cpu_utilization",
    "memory_utilization",
    "db_connections_in_use",
    "log_info",
    "log_warn",
    "log_error",
    "span_ms",
]
DEPLOYABLE = ["web-frontend", "api-gateway", "orders-service", "auth-service"]


@dataclass
class SimConfig:
    start: datetime
    days: float
    seed: int
    categories: list[str] = field(default_factory=lambda: [c for c in CATEGORIES if c != "memory_leak"])
    faults_per_day: float = 2.6
    noise_spikes_per_day: float = 8.0
    deploys_per_day: float = 1.3  # per deployable service, weekdays
    min_memory_leaks: int = 0
    platform_upgrade: bool = False  # permanently shifted baselines (for drift)


@dataclass
class Telemetry:
    metrics: pd.DataFrame  # long format: ts, service, SIGNALS...
    deploys: pd.DataFrame  # ts, service, version, kind
    faults: pd.DataFrame  # fault_id, category, service, start, end, detail
    logs: pd.DataFrame  # sampled structured log events: ts, service, level, message
    config: SimConfig

    def wide(self, signal: str) -> pd.DataFrame:
        return self.metrics.pivot(index="ts", columns="service", values=signal)[SERVICE_NAMES]


def _steps(minutes: float) -> int:
    return int(round(minutes * STEPS_PER_MINUTE))


class _Mods:
    """Per-service modifier arrays that faults write into."""

    def __init__(self, n: int):
        ones = {s: np.ones(n) for s in SERVICE_NAMES}
        zeros = {s: np.zeros(n) for s in SERVICE_NAMES}
        self.lat_mult = {s: v.copy() for s, v in ones.items()}
        self.tail_mult = {s: v.copy() for s, v in ones.items()}
        self.err_add = {s: v.copy() for s, v in zeros.items()}
        self.cpu_add = {s: v.copy() for s, v in zeros.items()}
        self.conn_add = np.zeros(n)
        self.edge_rps_mult = np.ones(n)
        self.mem_add = {s: v.copy() for s, v in zeros.items()}


def simulate(config: SimConfig) -> Telemetry:
    rng = np.random.default_rng(config.seed)
    n = int(config.days * STEPS_PER_DAY)
    ts = pd.date_range(config.start, periods=n, freq=f"{STEP_SECONDS}s")
    hours = ts.hour.to_numpy() + ts.minute.to_numpy() / 60
    weekend = ts.dayofweek.to_numpy() >= 5

    # --- load: daily and weekly seasonality with a slow random walk ---
    daily = 0.32 + 0.68 * (0.5 - 0.5 * np.cos(2 * np.pi * (hours - 4.0) / 24.0)) ** 1.25
    weekly = np.where(weekend, 0.72, 1.0)
    walk = np.zeros(n)
    shocks = rng.normal(0, 0.004, n)
    for i in range(1, n):  # AR(1), mean-reverting
        walk[i] = 0.9995 * walk[i - 1] + shocks[i]
    load = daily * weekly * np.exp(walk)

    mods = _Mods(n)
    faults, fault_logs = _inject_faults(config, rng, n, ts, mods)
    deploys = _deploys(config, rng, n, ts, faults)
    _noise_spikes(config, rng, n, mods)

    upgrade = config.platform_upgrade
    lat_shift = 1.2 if upgrade else 1.0
    orders_share, auth_share, external = (0.72, 0.28, 0.40) if upgrade else (0.6, 0.4, 0.25)

    # --- request rates through the call graph ---
    rps: dict[str, np.ndarray] = {}
    frontend = SERVICE_BY_NAME["web-frontend"].base_rps * load * mods.edge_rps_mult
    rps["web-frontend"] = frontend * rng.lognormal(0, 0.03, n)
    rps["api-gateway"] = (frontend * (1 + external)) * rng.lognormal(0, 0.03, n)
    rps["orders-service"] = rps["api-gateway"] * orders_share * rng.lognormal(0, 0.03, n)
    rps["auth-service"] = rps["api-gateway"] * auth_share * rng.lognormal(0, 0.03, n)
    rps["postgres-db"] = rps["orders-service"] * 3.0 * rng.lognormal(0, 0.03, n)

    # --- cpu and memory ---
    cpu, mem = {}, {}
    for s in SERVICES:
        peak = s.base_rps * (1 + external if s.name == "api-gateway" else 1)
        cpu[s.name] = np.clip(
            s.base_cpu
            + (0.05 if upgrade else 0)
            + 0.48 * rps[s.name] / peak
            + rng.normal(0, 0.015, n)
            + mods.cpu_add[s.name],
            0.02,
            1.0,
        )
        mem[s.name] = np.clip(
            s.base_memory + 0.02 * np.sin(2 * np.pi * hours / 24) + rng.normal(0, 0.004, n) + mods.mem_add[s.name],
            0.05,
            1.0,
        )

    # --- database connections (idle plus in-flight), saturation adds waiting connections ---
    conn = np.clip(20 + 0.09 * rps["postgres-db"] + rng.normal(0, 1.5, n) + mods.conn_add, 5, DB_POOL_MAX)

    # --- latency and errors, resolved from the bottom of the call graph up ---
    p50: dict[str, np.ndarray] = {}
    p95: dict[str, np.ndarray] = {}
    p99: dict[str, np.ndarray] = {}
    err: dict[str, np.ndarray] = {}
    own: dict[str, np.ndarray] = {}
    for name in RESOLVE_ORDER:
        s = SERVICE_BY_NAME[name]
        queueing = ((1 - 0.5) / np.maximum(1 - cpu[name], 0.03)) ** 0.55
        memory_pressure = 1 + 1.6 * np.maximum(0, mem[name] - 0.62)
        base_own = s.own_latency_ms * lat_shift * queueing * memory_pressure * mods.lat_mult[name]
        if name == "postgres-db":
            wait = 120 * np.maximum(0, (conn - 82) / 18) ** 2  # connection-wait time near the pool limit
            base_own = base_own + wait
        # callers retry failing dependencies, which costs them time
        retry = 1 + sum(3.0 * share * err[callee] for callee, share, _ in CALLS[name])
        own[name] = base_own * retry * rng.lognormal(0, 0.05, n)
        down50 = sum(share * calls * p50[callee] for callee, share, calls in CALLS[name])
        down95 = sum(share * calls * p95[callee] * 0.85 for callee, share, calls in CALLS[name])
        down99 = sum(share * calls * p99[callee] * 0.8 for callee, share, calls in CALLS[name])
        r95 = 2.1 * rng.lognormal(0, 0.04, n) * mods.tail_mult[name] ** 0.5
        r99 = 2.9 * rng.lognormal(0, 0.05, n) * mods.tail_mult[name]
        p50[name] = own[name] + down50
        p95[name] = own[name] * r95 + down95
        p99[name] = own[name] * r99 + down99

        e = s.base_error * rng.lognormal(0, 0.35, n) + mods.err_add[name]
        e = e + sum(0.85 * share * err[callee] for callee, share, _ in CALLS[name])
        e = e + 0.06 * np.maximum(0, cpu[name] - 0.9) / 0.1  # overload
        if name == "orders-service":
            e = e + 0.05 * np.maximum(0, conn - 97) / 3  # pool timeouts
        err[name] = np.clip(e, 0, 1)

    # --- logs (counts per step) ---
    rows = []
    for s in SERVICES:
        name = s.name
        volume = rps[name] * STEP_SECONDS
        frame = pd.DataFrame(
            {
                "ts": ts,
                "service": name,
                "request_rate": rps[name],
                "latency_p50": p50[name],
                "latency_p95": p95[name],
                "latency_p99": p99[name],
                "error_rate": err[name],
                "cpu_utilization": cpu[name],
                "memory_utilization": mem[name],
                "db_connections_in_use": conn if name == "postgres-db" else np.nan,
                "log_info": rng.poisson(volume * 0.02),
                "log_warn": rng.poisson(volume * (0.0005 + 0.2 * err[name])),
                "log_error": rng.poisson(volume * err[name] * 0.5),
                "span_ms": own[name],
            }
        )
        rows.append(frame)
    metrics = pd.concat(rows, ignore_index=True)
    float_cols = [c for c in SIGNALS if c not in ("log_info", "log_warn", "log_error")]
    metrics[float_cols] = metrics[float_cols].astype("float32")
    metrics["service"] = pd.Categorical(metrics["service"], categories=SERVICE_NAMES)

    logs = pd.DataFrame(fault_logs + _noise_logs(rng, ts), columns=["ts", "service", "level", "message"])
    return Telemetry(metrics, deploys, faults, logs.sort_values("ts", ignore_index=True), config)


# ---------------------------------------------------------------- faults


def _schedule(config: SimConfig, rng: np.random.Generator, n: int) -> list[tuple[int, str]]:
    """Fault start steps and categories, non-overlapping, with at least three hours between faults."""
    categories = list(config.categories)
    expected = config.faults_per_day * config.days
    count = max(1, rng.poisson(expected))
    chosen = [categories[i % len(categories)] for i in rng.permutation(count)]
    if config.min_memory_leaks and "memory_leak" in categories:
        missing = config.min_memory_leaks - chosen.count("memory_leak")
        for i in range(max(0, missing)):
            chosen[i] = "memory_leak"
    slots: list[tuple[int, str]] = []
    gap = 3 * 60 * STEPS_PER_MINUTE
    cursor = _steps(90)
    spacing = (n - cursor - _steps(8 * 60)) / max(len(chosen), 1)
    for category in chosen:
        jitter = int(rng.uniform(0.15, 0.85) * spacing)
        start = cursor + jitter
        if start >= n - _steps(60):
            break
        slots.append((start, category))
        span = _steps(12 * 60) if category == "memory_leak" else _steps(95)
        cursor = max(cursor + int(spacing), start + span + gap)
    return slots


def _inject_faults(config, rng, n, ts, mods: _Mods):
    faults, logs = [], []
    for index, (start, category) in enumerate(_schedule(config, rng, n)):
        fid = f"F{config.seed}-{index:03d}"
        if category == "database_saturation":
            service = "postgres-db"
            dur = _steps(rng.uniform(15, 45))
            ramp = _steps(rng.uniform(3, 8))
            end = min(start + dur, n)
            target = rng.uniform(55, 70)
            shape = np.clip((np.arange(end - start)) / max(ramp, 1), 0, 1)
            mods.conn_add[start:end] += target * shape
            mods.tail_mult["postgres-db"][start:end] *= 1 + 1.5 * shape
            detail = f"Connection pool filled by a long-running job (+{target:.0f} connections)."
            logs += _fault_log(ts, start, end, "postgres-db", "ERROR", "remaining connection slots are reserved", rng)
            logs += _fault_log(ts, start, end, "orders-service", "ERROR", "timeout acquiring connection from pool", rng)
        elif category == "deployment_regression":
            service = str(rng.choice(DEPLOYABLE))
            dur = _steps(rng.uniform(10, 40))
            lag = _steps(rng.uniform(1, 5))
            end = min(start + dur, n)
            bad = rng.uniform(0.02, 0.10)
            mods.err_add[service][start:end] += bad
            mods.lat_mult[service][start:end] *= rng.uniform(1.0, 1.5)
            detail = f"Release shipped a bug; error rate +{bad:.1%} until rollback."
            logs += _fault_log(
                ts, start, end, service, "ERROR", "unhandled exception in handler (release candidate)", rng
            )
            faults.append(_fault(fid, category, service, ts, start, end, detail, deploy_step=start - lag))
            continue
        elif category == "traffic_spike":
            service = "web-frontend"
            dur = _steps(rng.uniform(20, 90))
            end = min(start + dur, n)
            ramp = _steps(rng.uniform(2, 6))
            peak = rng.uniform(1.8, 2.8)
            shape = np.clip(np.arange(end - start) / max(ramp, 1), 0, 1)
            mods.edge_rps_mult[start:end] *= 1 + (peak - 1) * shape
            detail = f"Promotion drove traffic to {peak:.1f}x normal."
            logs += _fault_log(ts, start, end, "api-gateway", "WARN", "request queue above high-water mark", rng)
        elif category == "memory_leak":
            service = str(rng.choice(["orders-service", "auth-service", "api-gateway"]))
            climb = _steps(rng.uniform(3.5, 6) * 60)
            cycles = int(rng.integers(2, 4))
            end = min(start + climb * cycles, n)
            base = SERVICE_BY_NAME[service].base_memory
            headroom = 0.96 - base
            for c in range(cycles):
                a = start + c * climb
                b = min(a + climb, n)
                if a >= n:
                    break
                mods.mem_add[service][a:b] += headroom * np.linspace(0, 1, b - a)
                restart = slice(b, min(b + _steps(2), n))
                mods.err_add[service][restart] += 0.05
                mods.lat_mult[service][restart] *= 2.0
                logs += _fault_log(
                    ts, max(a, b - 8), min(b + 4, n), service, "ERROR", "container OOMKilled; restarting", rng
                )
            detail = f"Unbounded cache; memory climbs to the limit then restarts ({cycles} cycles)."
        else:  # dependency_failure
            service = str(rng.choice(["auth-service", "orders-service"]))
            dur = _steps(rng.uniform(10, 40))
            end = min(start + dur, n)
            bad = rng.uniform(0.12, 0.40)
            mods.err_add[service][start:end] += bad
            mods.lat_mult[service][start:end] *= 0.5  # fails fast
            detail = f"Downstream provider unavailable; {bad:.0%} of calls fail and callers retry."
            logs += _fault_log(ts, start, end, service, "ERROR", "upstream provider returned 503", rng)
            for caller in callers_of(service):
                logs += _fault_log(ts, start, end, caller, "ERROR", f"timeout calling {service}", rng)
            coincidental = rng.random() < 0.45
            faults.append(
                _fault(
                    fid,
                    category,
                    service,
                    ts,
                    start,
                    end,
                    detail,
                    nearby_deploy_step=start + _steps(rng.uniform(-10, 3)) if coincidental else None,
                )
            )
            continue
        faults.append(_fault(fid, category, service, ts, start, end, detail))
    frame = pd.DataFrame(faults)
    return frame, logs


def _fault(fid, category, service, ts, start, end, detail, deploy_step=None, nearby_deploy_step=None) -> dict:
    return {
        "fault_id": fid,
        "category": category,
        "service": service,
        "start": ts[start],
        "end": ts[min(end, len(ts) - 1)],
        "start_step": start,
        "end_step": end,
        "detail": detail,
        "deploy_step": deploy_step,
        "nearby_deploy_step": nearby_deploy_step,
    }


def _fault_log(ts, start, end, service, level, message, rng) -> list[tuple]:
    count = min(6, max(2, (end - start) // 20))
    picks = np.sort(rng.integers(start, max(start + 1, end), count))
    return [(ts[i], service, level, message) for i in picks]


def _noise_logs(rng, ts) -> list[tuple]:
    messages = [
        ("WARN", "slow query logged (>200ms)"),
        ("WARN", "retrying request after transient error"),
        ("ERROR", "client closed connection before response"),
        ("INFO", "config reloaded"),
    ]
    picks = rng.integers(0, len(ts), max(10, len(ts) // 2000))
    return [(ts[i], str(rng.choice(SERVICE_NAMES)), *messages[int(rng.integers(len(messages)))]) for i in picks]


# ---------------------------------------------------------------- deploys and noise


def _deploys(config, rng, n, ts, faults: pd.DataFrame) -> pd.DataFrame:
    rows = []
    versions = {s: [int(rng.integers(1, 9)), int(rng.integers(0, 20)), 0] for s in DEPLOYABLE}

    def bump(service: str) -> str:
        v = versions[service]
        v[2] += 1
        if rng.random() < 0.2:
            v[1], v[2] = v[1] + 1, 0
        return f"{v[0]}.{v[1]}.{v[2]}"

    days = int(np.ceil(config.days))
    for day in range(days):
        for service in DEPLOYABLE:
            first = day * STEPS_PER_DAY
            if first >= n or ts[first].dayofweek >= 5:
                continue
            for _ in range(rng.poisson(config.deploys_per_day)):
                step = first + _steps(rng.uniform(9, 17) * 60)
                if step < n:
                    rows.append((ts[step], service, bump(service), "deploy"))
    for fault in faults.to_dict("records"):
        if fault.get("deploy_step") is not None and not pd.isna(fault["deploy_step"]):
            step = int(fault["deploy_step"])
            rows.append((ts[max(step, 0)], fault["service"], bump(fault["service"]), "deploy"))
            end = min(int(fault["end_step"]), n - 1)
            v = versions[fault["service"]]
            rows.append((ts[end], fault["service"], f"{v[0]}.{v[1]}.{max(v[2] - 1, 0)}", "rollback"))
        if fault.get("nearby_deploy_step") is not None and not pd.isna(fault["nearby_deploy_step"]):
            step = int(np.clip(fault["nearby_deploy_step"], 0, n - 1))
            candidates = [c for c in callers_of(fault["service"]) if c in DEPLOYABLE] or DEPLOYABLE
            service = str(rng.choice(candidates))
            rows.append((ts[step], service, bump(service), "deploy"))
    frame = pd.DataFrame(rows, columns=["ts", "service", "version", "kind"])
    return frame.sort_values("ts", ignore_index=True)


def _noise_spikes(config, rng, n, mods: _Mods) -> None:
    """Short, harmless blips that are not incidents."""
    for _ in range(rng.poisson(config.noise_spikes_per_day * config.days)):
        start = int(rng.integers(0, n))
        end = min(n, start + int(rng.integers(2, 13)))  # 30 s to 3 min
        service = str(rng.choice(SERVICE_NAMES))
        kind = rng.integers(3)
        if kind == 0:
            mods.lat_mult[service][start:end] *= rng.uniform(1.5, 2.6)
        elif kind == 1:
            mods.err_add[service][start:end] += rng.uniform(0.008, 0.035)
        else:
            mods.cpu_add[service][start:end] += rng.uniform(0.15, 0.3)


def ground_truth(tel: Telemetry) -> pd.DataFrame:
    return tel.faults[["fault_id", "category", "service", "start", "end", "detail"]].copy()


def to_minutes(tel: Telemetry) -> pd.DataFrame:
    """1-minute rollups in long format (mean for rates, max for tails, sum for log counts)."""
    m = tel.metrics.copy()
    m["minute"] = m["ts"].dt.floor("min")
    agg = {
        "request_rate": "mean",
        "latency_p50": "mean",
        "latency_p95": "mean",
        "latency_p99": "max",
        "error_rate": "mean",
        "cpu_utilization": "mean",
        "memory_utilization": "mean",
        "db_connections_in_use": "max",
        "log_info": "sum",
        "log_warn": "sum",
        "log_error": "sum",
        "span_ms": "mean",
    }
    out = m.groupby(["minute", "service"], observed=True).agg(agg).reset_index()
    return out.rename(columns={"minute": "ts"})


def default_train(seed: int = 7) -> SimConfig:
    return SimConfig(start=datetime(2026, 7, 1), days=30, seed=seed)


def default_test(seed: int = 99) -> SimConfig:
    return SimConfig(
        start=datetime(2026, 7, 1) + timedelta(days=30),
        days=7,
        seed=seed,
        categories=list(CATEGORIES),
        min_memory_leaks=3,
        faults_per_day=3.4,
    )


def default_upgrade(seed: int = 303) -> SimConfig:
    return SimConfig(
        start=datetime(2026, 7, 1) + timedelta(days=37),
        days=10,
        seed=seed,
        categories=[c for c in CATEGORIES if c != "memory_leak"],
        platform_upgrade=True,
    )


def default_ops(seed: int = 404) -> SimConfig:
    """Production replay: the 30 days ending with the test week, at a realistic incident rate.

    Used for SLOs, error budgets and the live metrics replay. Training data is deliberately
    incident-dense (about 2.6 faults a day) so the classifier has examples; a real service with
    that many incidents would never meet a 99.9% SLO.
    """
    return SimConfig(
        start=datetime(2026, 7, 8),
        days=30,
        seed=seed,
        categories=list(CATEGORIES),
        faults_per_day=0.25,
        noise_spikes_per_day=8.0,
    )
