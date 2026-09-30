---
title: Runbook - Memory leak
doc_type: runbook
category: memory_leak
services: [orders-service, auth-service, api-gateway]
last_reviewed: 2026-06-30
---

# Runbook: Memory leak

## Symptoms

- `memory_utilization` on one service climbs slowly and steadily for hours instead of following the daily traffic pattern.
- Latency degrades gradually as garbage collection pauses grow.
- When memory reaches about 0.95 the container restarts, memory drops sharply, and the climb starts again.
- Error rate stays mostly normal until restarts begin, then shows short bursts during each restart.

## Checks

1. Plot memory for the last 24 hours. A leak is a steady upward slope that does not fall at night when traffic drops.
2. Count container restarts: `kubectl get pods -n shop -l app=<service>`. Restarts every few hours confirm the pattern.
3. Check whether the climb started after a deploy. Leaks often ship in a release but take hours to show, so look back up to 48 hours, not 15 minutes.
4. Take a heap snapshot from one replica before it restarts and attach it to the incident.

## Mitigation

1. Short term: schedule a rolling restart every 6 hours to keep memory below 0.9: `kubectl rollout restart deployment/<service> -n shop`.
2. If the leak started with a release, roll back to the previous version.
3. Raise the container memory limit only as a last resort and only by one step (from 1 GiB to 1.5 GiB); it delays restarts but does not fix the leak.

## Escalation

- Open a ticket for the service owner with the heap snapshot and the memory graph.
- Page the service owner only if restarts happen more often than once an hour.
