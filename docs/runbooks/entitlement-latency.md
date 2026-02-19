# Runbook: Entitlement latency (stub)

**Status:** stub — spec §8.5 alert says “check Redis / DB pool”; full ops scenario grows as metrics are wired.
**Alert:** EntitlementLatency (p99 `entitlement.evaluate` > 100 ms for 5 min)
**Priority:** P3

## Symptoms

- [ ] Rising p99 / p95 `entitlement.evaluate`
- [ ] Falling `entitlement_cache_hit_ratio`
- [ ] Rising `db.query` / Redis latency in traces
- [ ] Product tickets: “limits are slow”

## Quick checks

- [ ] Redis reachable (`/health/ready` or ping); no connection storm
- [ ] PostgreSQL pool: saturation, long queries on entitlement / subscription read
- [ ] Version bump / stampede after mass invalidate (ADR-003)
- [ ] Evaluate not mixed with usage write on same hot path
- [ ] `correlation_id` of slow requests in logs / OTel

## Safe actions

- [ ] Restore Redis; when down — circuit → direct DB (expected higher latency)
- [ ] Increase/fix pool size only after diagnosis (not blind prod change)
- [ ] Verify evaluate does **not** read Kafka
- [ ] Locally: `docker compose -p billing-platform -f deploy/compose/docker-compose.yml restart redis` / API after config fix

## Do not

- [ ] Read entitlements from Kafka “for speed”
- [ ] Disable tenant filter
- [ ] UPDATE ledger / subscriptions “to respond faster”

## Escalation

| Severity | Condition | Action |
|----------|-----------|--------|
| P3 | isolated spikes, cache miss | on-call Redis/DB + monitor |
| P2 | p99 stable > SLO UX 15 min+ | eng; ADR-003 / pool |
| P1 | evaluate mass 5xx | see also [`ready-probe-fail.md`](ready-probe-fail.md) |

## Related documents

- ADR-003, `docs/slo.md`, `spec.md` §8.5 / §4.4 (Redis down)
