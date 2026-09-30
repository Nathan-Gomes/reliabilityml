---
title: Postmortem PM-2026-01 - Identity provider outage cascaded to checkout
doc_type: postmortem
category: dependency_failure
services: [auth-service, api-gateway, web-frontend]
last_reviewed: 2026-02-03
---

# PM-2026-01: Identity provider outage cascaded to checkout

## Summary

The external identity provider failed for 26 minutes. `auth-service` returned errors quickly, but `api-gateway` retried each call three times, tripling load and pushing gateway latency above 2 seconds. Logins and checkout failed for 30% of users.

## Timeline

- 08:12 Identity provider began returning 503s.
- 08:14 `auth-service` error rate at 40%; gateway latency rising.
- 08:19 `web-frontend` errors paged; on-call started with the front end.
- 08:31 Traces showed `auth-service` spans failing first; circuit breaker enabled.
- 08:38 Provider recovered; breaker closed at 08:45.

## Root cause

External provider outage, amplified by gateway retries without a circuit breaker.

## Action items

- Enable the gateway circuit breaker by default for `auth-service` (done).
- Serve cached sessions for up to 15 minutes when auth fails (done).
- Start investigations from the deepest failing service in traces, not the service that paged.
