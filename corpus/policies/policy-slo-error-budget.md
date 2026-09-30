---
title: Policy - SLOs and error budget
doc_type: policy
category: slo
services: [web-frontend, api-gateway, orders-service, auth-service, postgres-db]
last_reviewed: 2026-09-01
---

# Policy: SLOs and error budget

## Targets

- Availability SLO: 99.9% of requests succeed over a rolling 30-day window. This allows about 43.2 minutes of full downtime per 30 days.
- Latency SLO: 99% of requests complete in under 300 ms over a rolling 30-day window.
- Both SLOs apply to every user-facing service: `web-frontend`, `api-gateway`, `orders-service` and `auth-service`.

## Burn-rate alerts

- Page when the burn rate exceeds 14.4 on both the 1-hour and 5-minute windows (2% of the budget spent in one hour).
- Page when the burn rate exceeds 6 on both the 6-hour and 30-minute windows (5% of the budget in six hours).
- Open a ticket when the burn rate exceeds 1 on both the 3-day and 6-hour windows (10% of the budget in three days).

## Error budget policy

- Above 50% of the budget remaining: normal release cadence.
- Between 25% and 50% remaining: releases need a second reviewer and a rollback plan in the change ticket.
- Below 25% remaining: freeze non-urgent releases. Only fixes for reliability or security may ship until the budget recovers above 25%.
- Budget exhausted: the owning team spends the next sprint on reliability work agreed with the SRE lead.
