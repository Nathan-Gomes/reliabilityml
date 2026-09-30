---
title: Policy - Deployments and rollback
doc_type: policy
category: deployment
services: [web-frontend, api-gateway, orders-service, auth-service]
last_reviewed: 2026-08-19
---

# Policy: Deployments and rollback

## Release rules

- Every production deploy passes the deployment validation gate: tests, a canary replay, model checks and an error-budget check.
- The canary replay compares the candidate with the current version on a recorded telemetry slice. The candidate fails if its error rate is more than 0.2 percentage points higher, or its p95 latency is more than 10% higher.
- No deploys between 16:00 on Friday and 09:00 on Monday unless they fix an active incident.

## Rollback

- Any engineer on call may roll back a release without approval: `kubectl rollout undo deployment/<service> -n shop`.
- Roll back first when a release is suspected; a rollback that turns out unnecessary costs minutes, a delayed rollback costs error budget.
- After a rollback, pause the service's release pipeline until the owning team reviews the failure.

## Change records

- Each deploy records the service, version, author and change ticket in the deployments table.
- Rollbacks are recorded as deploys of the previous version, tagged `rollback`.
