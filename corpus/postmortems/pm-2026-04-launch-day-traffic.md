---
title: Postmortem PM-2026-04 - Launch-day traffic exceeded autoscaling limits
doc_type: postmortem
category: traffic_spike
services: [web-frontend, api-gateway, orders-service]
last_reviewed: 2026-04-20
---

# PM-2026-04: Launch-day traffic exceeded autoscaling limits

## Summary

A partner promotion tripled traffic for two hours. The gateway hit its 12-replica autoscaling limit, CPU stayed above 0.9 and p95 latency tripled. About 2% of requests failed.

## Timeline

- 18:00 Promotion email sent; request rate rose 180% within 15 minutes.
- 18:09 CPU on `api-gateway` above 0.9; the 1-hour burn-rate alert paged.
- 18:20 On-call raised the gateway maximum to 20 replicas.
- 18:35 Latency recovered; traffic stayed high until 20:00 without errors.

## Root cause

The campaign was not shared with the platform team, and the autoscaling limit had not been reviewed since the previous year.

## Action items

- Marketing campaigns expected to double traffic must be shared a week ahead.
- Review autoscaling maximums quarterly.
