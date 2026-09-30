---
title: Runbook - Dependency failure
doc_type: runbook
category: dependency_failure
services: [auth-service, api-gateway, orders-service, web-frontend]
last_reviewed: 2026-08-28
---

# Runbook: Dependency failure

## Symptoms

- One service starts returning errors, and within a few minutes its callers show errors too.
- Errors spread upstream along the call chain: `auth-service` or `orders-service` first, then `api-gateway`, then `web-frontend`.
- The failing service often shows low latency (fast failures) while callers show high latency from timeouts and retries.
- It can look like a deployment regression when a deploy happened nearby on any service in the chain.

## Checks

1. Find the deepest service in the call chain with an elevated error rate. That is the likely origin; services above it are victims.
2. In the trace view, look for the span that returns errors first. The origin's span fails; the callers' spans time out.
3. Check whether the origin service itself was deployed recently. If it was, use the deployment regression runbook. If only a caller was deployed, the deploy is probably a coincidence.
4. Check the origin's own dependencies (for `auth-service`, the identity provider status page; for `orders-service`, `postgres-db`).

## Mitigation

1. Enable the gateway circuit breaker for the failing dependency so callers fail fast instead of piling up retries: `kubectl set env deployment/api-gateway CIRCUIT_BREAKER_AUTH=open -n shop`.
2. For `auth-service` failures, the gateway can serve cached sessions for up to 15 minutes with `SESSION_CACHE_FALLBACK=true`.
3. Restart the origin service only after confirming its own dependencies are healthy.

## Escalation

- Page the owner of the origin service, not the owners of every service showing errors.
- If the origin is an external provider, post a status update and escalate through the vendor contact listed in the escalation policy.
