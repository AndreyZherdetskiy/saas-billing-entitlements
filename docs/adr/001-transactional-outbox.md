# ADR-001: Transactional outbox

- **Status:** Accepted
- **Date:** 2026-02-10
- **Spec:** §4.3.2–4.3.3, §12.2

## Context

Dual-write `UPDATE domain` + separate `kafka.publish` yields either a lost fact (crash after commit before produce) or a false fact (produce before rollback). For billing, losing activation/payment facts is unacceptable.

## Decision

1. In a **single** PostgreSQL transaction: domain changes + (when needed) ledger + `INSERT INTO outbox_messages`.
2. A separate **`outbox-relay`** process (not Celery) selects `published_at IS NULL AND publish_attempts < 10` with `FOR UPDATE SKIP LOCKED`, publishes to Kafka, and sets `published_at`.
3. Kafka message key = `outbox_messages.id` (BIGINT). Partition key = `organization_id` (internal / stable string partition key).
4. Attempts ≥ 10 → `outbox_dead_letters` + alert.
5. Delivery semantics: **at-least-once**; consumers are idempotent on `event_id`.
6. **`outbox_messages.idempotency_key` naming:** globally UNIQUE in the database; prefixes are **intentionally** domain-specific (not a single `{aggregate}:{event}:{version}` template from spec §6.4). Examples: `webhook:{webhook_id}:subscription.activated`, `plan_change:{idem}:plan_changed`, `subscription:{public_id}:trial_started`. Webhook-scoped prefixes isolate reprocessing of one `webhook_events` row from other aggregates; dedup via `ON CONFLICT DO NOTHING` on `idempotency_key` (`services/outbox.py`).

## Consequences

- Reliable integration contract without dual-write.
- One more deployable unit (relay) in Compose/Helm.
- Consumers must deduplicate.
- Forbidden: publishing domain facts from Celery/API after commit without the outbox.

## Alternatives considered

- Debezium CDC — powerful, heavier ops in stage 1; raw row changes instead of versioned domain events.
- "Publish after commit" — classic dual-write risk.
- LISTEN/NOTIFY — insufficient for multi-consumer / replay.

## Links

- ADR-002 (Kafka bus), ADR-004 (Celery vs relay), ADR-010 (outbox BIGINT PK)
