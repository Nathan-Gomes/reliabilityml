---
title: Runbook - Traffic spike
doc_type: runbook
category: traffic_spike
services: [web-frontend, api-gateway, orders-service]
last_reviewed: 2026-07-21
---

# Runbook: Traffic spike

## Symptoms

- `request_rate` on `web-frontend` and `api-gateway` rises more than 60% above the same hour last week.
- CPU utilization rises on the front-end tiers first, often above 0.8.
- Latency rises across the chain, which can look like database saturation.
- Database connections rise in proportion to traffic but stay below 85% of the pool.

## Checks

1. Compare request rate with the same hour last week. A spike is defined by traffic, not by latency.
2. Check `db_connections_in_use`. If the pool is above 85% while traffic is near normal, this is database saturation, not a spike.
3. Look for a marketing event, a partner integration going live, or a single client IP range generating most requests in the gateway access logs.
4. Check whether autoscaling has reached its maximum replica count: `kubectl get hpa -n shop`.

## Mitigation

1. If autoscaling is capped, raise the maximum for `web-frontend` and `api-gateway` from 12 to 20 replicas: `kubectl patch hpa api-gateway -n shop -p '{"spec":{"maxReplicas":20}}'`.
2. If one client dominates traffic, apply the gateway rate limit of 200 requests per second per API key.
3. Serve the cached product catalogue from the CDN to reduce load on `orders-service`.

## Escalation

- Escalate to the platform on-call if CPU stays above 0.9 for 10 minutes after scaling.
- Notify the business owner when a spike is caused by an external campaign so capacity can be planned next time.
