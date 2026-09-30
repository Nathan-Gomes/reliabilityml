---
title: Runbook - Deployment regression
doc_type: runbook
category: deployment_regression
services: [web-frontend, api-gateway, orders-service, auth-service]
last_reviewed: 2026-09-02
---

# Runbook: Deployment regression

## Symptoms

- Error rate on one service jumps within 15 minutes of a `deployment_event` for that service.
- The jump is a step, not a slow climb: error rate moves from under 0.5% to several percent within two or three windows.
- Latency may rise on the same service, but request rate stays normal.
- ERROR-level log events carry the new version string.

## Checks

1. Find the most recent deploy on the affected service and its direct dependencies in the deployments table or with `kubectl rollout history deployment/<service> -n shop`.
2. Compare the error rate in the 15 minutes before and after the deploy. A regression shows a clear step at the deploy time.
3. Check whether the errors are confined to the deployed service. If callers fail but the deployed service is healthy, see the dependency failure runbook instead.
4. Most deploys are clean. Do not assume a deploy is the cause just because one happened; the step change and the version string in logs are the evidence.

## Mitigation

1. Roll back first, investigate second: `kubectl rollout undo deployment/<service> -n shop`.
2. Pause the release pipeline for that service so the build is not redeployed automatically.
3. Confirm recovery: error rate should return below 0.5% within 10 minutes of the rollback completing.

## Escalation

- If rollback does not restore the error rate within 10 minutes, escalate to the service owner and treat the deploy as a coincidence; continue with general triage.
- Every rollback of a production release needs a ticket and a short written note linking the failing version.
