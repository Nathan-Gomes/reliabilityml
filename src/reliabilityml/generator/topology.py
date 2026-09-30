"""The simulated system: five services, their call graph and their normal behaviour."""

from __future__ import annotations

from dataclasses import dataclass

STEP_SECONDS = 15
STEPS_PER_MINUTE = 60 // STEP_SECONDS
STEPS_PER_DAY = 24 * 60 * STEPS_PER_MINUTE
DB_POOL_MAX = 100


@dataclass(frozen=True)
class Service:
    name: str
    depth: int  # 0 = edge; larger = deeper in the call chain
    base_rps: float  # requests per second at the daily peak
    own_latency_ms: float  # median time spent inside this service
    base_error: float
    base_cpu: float
    base_memory: float


SERVICES: list[Service] = [
    Service("web-frontend", 0, 120.0, 24.0, 0.00007, 0.18, 0.42),
    Service("api-gateway", 1, 150.0, 14.0, 0.00006, 0.20, 0.38),
    Service("orders-service", 2, 90.0, 30.0, 0.00007, 0.22, 0.46),
    Service("auth-service", 2, 60.0, 14.0, 0.00005, 0.15, 0.35),
    Service("postgres-db", 3, 260.0, 5.0, 0.00002, 0.25, 0.55),
]
SERVICE_NAMES = [s.name for s in SERVICES]
SERVICE_BY_NAME = {s.name: s for s in SERVICES}

# caller -> [(callee, share of caller requests that call it, calls per request)]
CALLS: dict[str, list[tuple[str, float, float]]] = {
    "web-frontend": [("api-gateway", 1.0, 1.0)],
    "api-gateway": [("orders-service", 0.6, 1.0), ("auth-service", 0.4, 1.0)],
    "orders-service": [("postgres-db", 1.0, 3.0)],
    "auth-service": [],
    "postgres-db": [],
}
# Order in which latency and errors are resolved: callees before callers.
RESOLVE_ORDER = ["postgres-db", "auth-service", "orders-service", "api-gateway", "web-frontend"]


def callers_of(service: str) -> list[str]:
    return [caller for caller, edges in CALLS.items() if any(callee == service for callee, _, _ in edges)]


def dependencies_of(service: str) -> list[str]:
    """All services below this one in the call graph (transitive)."""
    found: list[str] = []
    stack = [callee for callee, _, _ in CALLS[service]]
    while stack:
        name = stack.pop()
        if name not in found:
            found.append(name)
            stack.extend(callee for callee, _, _ in CALLS[name])
    return found


CATEGORIES = [
    "database_saturation",
    "deployment_regression",
    "traffic_spike",
    "memory_leak",
    "dependency_failure",
]
