---
title: Postmortem PM-2026-06 - Front-end release doubled API calls
doc_type: postmortem
category: deployment_regression
services: [web-frontend, api-gateway]
last_reviewed: 2026-06-25
---

# PM-2026-06: Front-end release doubled API calls

## Summary

A `web-frontend` release fired the cart request twice on every page load. Gateway latency rose 35% and error rate reached 1.8% from throttling. It was first mistaken for a traffic spike.

## Timeline

- 13:05 `web-frontend` 9.3.1 deployed.
- 13:12 Gateway request rate up 90%; on-call began the traffic spike runbook and scaled the gateway.
- 13:31 Scaling did not help; an engineer noticed the rise started exactly at the deploy.
- 13:36 Rolled back; request rate returned to normal immediately.

## Root cause

A React effect ran on every render instead of once, duplicating the cart request.

## Action items

- When request rate jumps, check for a front-end deploy before treating it as organic traffic.
- Compare requests per page view in the canary replay.
