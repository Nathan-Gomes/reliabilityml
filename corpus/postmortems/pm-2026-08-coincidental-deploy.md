---
title: Postmortem PM-2026-08 - Payment dependency failure blamed on unrelated deploy
doc_type: postmortem
category: dependency_failure
services: [orders-service, api-gateway]
last_reviewed: 2026-09-04
---

# PM-2026-08: Payment dependency failure blamed on unrelated deploy

## Summary

`orders-service` began failing calls to its payment dependency eight minutes after an unrelated `api-gateway` deploy. On-call rolled back the gateway, which did not help, costing 20 minutes before the real cause was found.

## Timeline

- 15:40 `api-gateway` 6.1.4 deployed (a logging change).
- 15:48 `orders-service` error rate rose to 5%; gateway errors followed.
- 15:52 On-call rolled back the gateway.
- 16:05 Errors unchanged; traces showed the payment call inside `orders-service` failing first.
- 16:12 Payment provider confirmed a regional incident; orders queued for retry.

## Root cause

External payment provider incident. The nearby deploy was a coincidence.

## Action items

- The deployment regression runbook now requires the error step to be on the deployed service itself.
- The incident classifier uses "deploy on the affected service" separately from "deploy anywhere nearby".
