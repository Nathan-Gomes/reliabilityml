---
title: Policy - On-call and escalation
doc_type: policy
category: escalation
services: [web-frontend, api-gateway, orders-service, auth-service, postgres-db]
last_reviewed: 2026-07-30
---

# Policy: On-call and escalation

## Roles

- Primary on-call: acknowledges pages within 5 minutes and leads the incident until an incident commander is named.
- Incident commander: named for any incident expected to last more than 30 minutes or affecting more than one service.
- Database on-call: owns `postgres-db`; paged for saturation lasting more than 15 minutes after mitigation or replication lag above 30 seconds.

## Severity

- SEV1: checkout or login unavailable for most users. Page primary on-call and the incident commander rotation immediately.
- SEV2: degraded experience or a single service failing with a workaround. Page primary on-call.
- SEV3: no user impact yet, for example a ticket-level burn-rate alert. Handle in working hours.

## Communication

- Post updates in the incident channel every 15 minutes for SEV1 and every 30 minutes for SEV2.
- External providers are escalated through the vendor contact sheet maintained by the platform team.
- Every SEV1 and SEV2 incident gets a blameless postmortem within five working days.
