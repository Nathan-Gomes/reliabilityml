---
title: Postmortem PM-2026-05 - Orders release leaked database connections
doc_type: postmortem
category: database_saturation
services: [orders-service, postgres-db]
last_reviewed: 2026-05-22
---

# PM-2026-05: Orders release leaked database connections

## Summary

Release 4.12.0 of `orders-service` failed to return connections on an error path. Over 50 minutes the pool filled, and 6% of order requests timed out.

## Timeline

- 10:15 `orders-service` 4.12.0 deployed. Error rate unchanged.
- 10:40 `db_connections_in_use` passed 85; latency began rising.
- 11:02 Anomaly detector alerted; the classifier labelled it `database_saturation`.
- 11:09 On-call noticed the connection climb began after the deploy and rolled back.
- 11:18 Connections dropped to normal.

## Root cause

A new retry path acquired a connection before a validation step and never released it when validation failed.

## Action items

- Add a connection-leak test to the `orders-service` integration suite.
- Include database connections in the canary replay comparison, not just error rate and latency.
- Teach on-call to check for deploys up to 60 minutes back when connections climb slowly.
