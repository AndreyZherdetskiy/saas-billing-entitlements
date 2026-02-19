# Runbook: Ready probe fail (stub)

**Status:** stub — spec §8.5 “availability incident”; K8s/Helm detail at stage 3.
**Alert:** ReadyProbeFail (`/health/ready` fails > 2 min)
**Priority:** P1

## Symptoms

- [ ] Readiness probe red > 2 min
- [ ] New API replicas not accepting traffic / rolling update stuck
- [ ] Clients: 503 / connection refused on Admin/API
- [ ] Compose: `api` unhealthy

## Quick checks

- [ ] PostgreSQL primary reachable (required ready dependency)
- [ ] Redis (if in ready path) — up or intentionally optional
- [ ] Kafka: with `HEALTH_KAFKA_OPTIONAL=true` ready may be green with degraded Kafka — verify config
- [ ] API logs: startup errors / pool / migration pending
- [ ] Recent deploy / `alembic upgrade` incomplete

## Safe actions

- [ ] Restore PG (without it writes and webhooks are unavailable)
- [ ] Restart API **after** dependencies restored:
  `docker compose -p billing-platform -f deploy/compose/docker-compose.yml restart api`
- [ ] Do not send traffic to instance with failed ready (load balancer / compose health)
- [ ] Persist-first webhooks: after recovery — check backlog / failed webhooks ([`webhook-replay.md`](webhook-replay.md))

## Do not

- [ ] Remove readiness checks “to turn green”
- [ ] Failover writes to read replica without stage 3 runbook
- [ ] Dual-write / manual Kafka publish during outage

## Escalation

| Severity | Condition | Action |
|----------|-----------|--------|
| P1 | ready fails > 2 min | incident; freeze deploy; restore PG/Redis |
| P2 | flapping ready < 2 min | eng + dependencies / probes |
| P3 | single local compose glitch | restart + logs |

## Related documents

- `spec.md` §8.6 (health / ready / graceful shutdown), §4.4 (Primary DB down)
- `docs/slo.md`, [`outbox-lag.md`](outbox-lag.md), [`webhook-replay.md`](webhook-replay.md)
