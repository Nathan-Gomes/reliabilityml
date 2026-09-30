---
title: Runbook - Triage for unclassified alerts
doc_type: runbook
category: unknown
services: [web-frontend, api-gateway, orders-service, auth-service, postgres-db]
last_reviewed: 2026-09-10
---

# Runbook: Triage for unclassified alerts

## Symptoms

- An anomaly alert fired but the incident classifier returned `unknown`, meaning no known category reached the confidence threshold.
- This is expected for new failure patterns, and for patterns the classifier was never trained on.

## Checks

1. Identify the first service whose signals moved, using the incident evidence panel. Work outward from that service.
2. Check the shape of the change: a step change suggests a deploy or dependency; a slow climb suggests a leak or gradual saturation; a spike in request rate suggests traffic.
3. Compare memory, CPU and connection signals over the last 24 hours, not just the last hour. Slow patterns are invisible in short windows.
4. Search past postmortems for the affected service; similar incidents often recur.

## Mitigation

1. If a deploy happened on the affected service in the last hour and the error rate stepped up, roll back.
2. Otherwise stabilize before diagnosing: scale out the affected service by two replicas and watch whether the symptoms ease.
3. Record what you find in the incident; unknown incidents are the best source of new training labels.

## Escalation

- Page the owner of the first affected service if the error budget burn rate exceeds 6 on the 6-hour window.
- After the incident, label it and add it to the classifier training set.
