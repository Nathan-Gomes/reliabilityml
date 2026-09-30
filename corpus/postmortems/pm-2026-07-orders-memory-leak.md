---
title: Postmortem PM-2026-07 - Orders service memory leak caused restart loop
doc_type: postmortem
category: memory_leak
services: [orders-service]
last_reviewed: 2026-07-28
---

# PM-2026-07: Orders service memory leak caused restart loop

## Summary

A caching change in `orders-service` never evicted entries. Memory climbed for nine hours, then containers restarted every 90 minutes, each restart causing a short burst of errors. The anomaly classifier returned `unknown` because no leak had been seen in its training data.

## Timeline

- Day 1 22:00 `orders-service` 4.15.0 deployed. No immediate change.
- Day 2 07:10 First container restart at 0.95 memory utilization.
- Day 2 09:30 Third restart; alert fired and was classified `unknown`.
- Day 2 10:05 On-call followed the unclassified triage runbook, looked at 24 hours of memory and saw the steady climb.
- Day 2 10:20 Rolled back to 4.14.2; memory flattened.

## Root cause

An in-process cache keyed by request ID grew without bound.

## Action items

- Add a memory-growth test that runs the service for 30 minutes under load in CI.
- Use the unclassified triage runbook's 24-hour look-back for any restart loop.
- Add this incident as a labelled `memory_leak` example for classifier training.
