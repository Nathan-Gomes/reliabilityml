---
title: Runbook - Database connection saturation
doc_type: runbook
category: database_saturation
services: [postgres-db, orders-service, api-gateway]
last_reviewed: 2026-08-14
---

# Runbook: Database connection saturation

## Symptoms

- `db_connections_in_use` on `postgres-db` above 85 of the 100-connection pool for more than 3 minutes.
- p99 latency on `orders-service` climbs first; p95 follows within a few minutes.
- Error rate rises upstream on `api-gateway` and `web-frontend` as requests time out waiting for a connection.
- CPU on `postgres-db` is often normal. High connections with normal CPU points to waiting, not heavy queries.

## Checks

1. Confirm saturation, not a traffic spike: compare `request_rate` on `api-gateway` with the same hour last week. If traffic is within 20% of normal, treat this as saturation.
2. Check whether a deployment of `orders-service` happened in the last 30 minutes. A new release that forgets to return connections looks exactly like this.
3. Look at the trace view for the slowest span. If the `postgres-db` query span dominates, the pool is the bottleneck.
4. Check for long-running transactions: `SELECT pid, now() - xact_start AS age, state FROM pg_stat_activity ORDER BY age DESC LIMIT 20;`

## Mitigation

1. If a long-running transaction holds locks, terminate it with `SELECT pg_terminate_backend(<pid>);` after recording the query text in the incident channel.
2. If an `orders-service` deploy is the trigger, roll it back: `kubectl rollout undo deployment/orders-service -n shop`.
3. Temporarily lower `orders-service` worker concurrency from 32 to 16 to reduce pool pressure: `kubectl set env deployment/orders-service WORKER_CONCURRENCY=16 -n shop`.
4. Do not raise the pool maximum above 100 during an incident. The database host is sized for 100 connections and raising it moves the failure into Postgres itself.

## Escalation

- Page the database on-call if saturation lasts more than 15 minutes after mitigation, or if replication lag exceeds 30 seconds.
- Open a ticket for the `orders-service` team if connection leaks are confirmed in a release.
