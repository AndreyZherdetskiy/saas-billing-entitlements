# Runbook: Outbox lag

**Status:** Task 8 implemented (relay live in Compose)
**Alert:** OutboxLagHigh (`outbox_lag_seconds` > 300)

## Symptoms

- [ ] Rising `outbox_unpublished_count` / `outbox_lag_seconds`
- [ ] Downstream consumers not receiving `subscription.*`, `ledger.entry_posted`, `reconciliation.*`
- [ ] Kafka UI / consumer lag growing while API is healthy
- [ ] `outbox-relay` logs: repeated publish errors, `last_error` on outbox rows

## Quick checks

- [ ] Container `outbox-relay` in `running` state (`docker compose -p billing-platform -f deploy/compose/docker-compose.yml ps outbox-relay`)
- [ ] Kafka bootstrap reachable (`KAFKA_BOOTSTRAP_SERVERS`, topics created by `kafka-init`)
- [ ] PostgreSQL reachable; no long locks on `outbox_messages`
- [ ] Rows with `publish_attempts` ≥ `OUTBOX_MAX_ATTEMPTS` → dead letters / `failed`
- [ ] `/health/ready` API: Kafka check (or `degraded` when `HEALTH_KAFKA_OPTIONAL=true`)
- [ ] `correlation_id` in relay logs for recent errors

## At-least-once: publish inside TX (crash window)

Relay (`outbox_relay/publisher.py`) publishes to Kafka **inside** the same DB transaction as `published_at` / DLQ move (before `commit`). This is an intentional transactional outbox trade-off:

| Event | Effect |
|-------|--------|
| Kafka ACK **before** commit, then relay crash | Consumer may have received the event; outbox row still without `published_at` → **republish** after restart (at-least-once) |
| Commit **without** Kafka (rare split-brain) | Lag grows; relay retry — see symptoms above |

**Ops expectation:** downstream consumers are idempotent on `outbox_message.id` / business keys (ADR-001). Do not assume exactly-once at the Kafka boundary. Publish-after-commit is a possible future amendment; document the window now, not dual-write.

## Safe actions

- [ ] Restart relay: `docker compose -p billing-platform -f deploy/compose/docker-compose.yml restart outbox-relay` (at-least-once; consumers must be idempotent)
- [ ] Ensure only one active relay writes to the partition (stage 1: single replica)
- [ ] Triage `outbox_dead_letters` / failed rows manually; replay after fixing poison payload
- [ ] Temporarily increase `OUTBOX_BATCH_SIZE` only after assessing PG/Kafka load
- [ ] Escalate to eng if lag > SLO 15 min (see `docs/slo.md`)

## Do not

- [ ] Publish events to Kafka bypassing outbox (dual-write)
- [ ] Use Celery beat as a relay replacement for domain facts
- [ ] DELETE from `outbox_messages` without audit / postmortem
- [ ] UPDATE domain tables “to catch up” events

## Escalation

| Severity | Condition | Action |
|----------|-----------|--------|
| P3 | lag < 15 min, relay recoverable | on-call restart + monitor |
| P2 | lag > 15 min or DLQ growing | eng + check poison messages |
| P1 | Kafka/PG unavailable | incident; freeze deploy |

## Related documents

- ADR-001 (transactional outbox), ADR-004 (relay vs Celery)
- `docs/runbooks/webhook-replay.md` (if lag from failed webhook → outbox enqueue)
