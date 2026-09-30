---
title: Postmortem PM-2026-02 - Checkout slowed by connection pool exhaustion
doc_type: postmortem
category: database_saturation
services: [postgres-db, orders-service, api-gateway]
last_reviewed: 2026-03-06
---

# PM-2026-02: Checkout slowed by connection pool exhaustion

## Summary

For 38 minutes on 18 February, checkout requests were slow and 4% failed. `postgres-db` connections sat at the 100-connection limit while database CPU stayed below 40%.

## Timeline

- 14:02 A nightly reporting job started early and opened 40 long-lived connections.
- 14:09 p99 latency on `orders-service` passed 900 ms; the latency SLO burn-rate alert paged on-call.
- 14:21 On-call confirmed traffic was normal and found the reporting job in `pg_stat_activity`.
- 14:31 The job was stopped; connections fell to 55 within two minutes.
- 14:40 Latency and error rate returned to normal.

## Root cause

The reporting job used the production connection pool instead of the read replica, holding connections for minutes at a time.

## Action items

- Move reporting jobs to the read replica with their own pool (done).
- Alert when any single client holds more than 20 connections for five minutes.
- Add the "high connections, normal CPU" pattern to the database saturation runbook (done).
