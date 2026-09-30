---
title: Postmortem PM-2026-03 - Auth release returned 500s on token refresh
doc_type: postmortem
category: deployment_regression
services: [auth-service, api-gateway]
last_reviewed: 2026-04-02
---

# PM-2026-03: Auth release returned 500s on token refresh

## Summary

`auth-service` 2.7.0 broke token refresh for sessions older than 24 hours. Error rate on `auth-service` stepped from 0.2% to 7% within four minutes of the deploy.

## Timeline

- 09:30 `auth-service` 2.7.0 deployed.
- 09:34 Error rate step detected; classifier labelled it `deployment_regression` with 0.91 confidence.
- 09:38 On-call rolled back with `kubectl rollout undo deployment/auth-service -n shop`.
- 09:44 Error rate back below 0.3%.

## Root cause

A library upgrade changed the token timestamp format; older tokens failed to parse.

## Action items

- Add long-lived session tokens to the canary replay data set.
- Keep the "roll back first" rule; the 8-minute rollback limited budget spend to 3%.
