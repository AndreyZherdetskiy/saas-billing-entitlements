# ADR-002: Kafka as integration bus

- **Status:** Accepted
- **Date:** 2026-02-10
- **Spec:** §4.3.1, §12.1

## Context

We need a contract for data marts, RevOps automation, dunning notifier, and analytics. At the same time, the hot-path authorize (evaluate) requires millisecond latency and consistency with the current subscription.

## Decision

1. **PostgreSQL** — source of truth for operational entitlement reads and platform financial facts.
2. **Kafka** — bus for **facts after commit** (via outbox), not a source for authorize.
3. Stage 1 topics (minimum): `billing.subscription.events`, `billing.invoice.events`, `billing.ledger.events`, `billing.reconciliation.events`, `billing.entitlement.events`, `billing.dlq`.
4. Event envelope v1: `schema_version`, `event_id`, `event_type`, `occurred_at`, `organization_id`, `correlation_id`, `payload`.
5. Stage 1 producer: **aiokafka** in `outbox-relay`.

## Consequences

- CQRS split: sync read entitlements ≠ async integration.
- Ops: broker + lag monitoring.
- Forbidden: evaluate / authorize reads Kafka consumer state.

## Alternatives considered

- PG NOTIFY / Redis Streams — weaker multi-consumer support, retention, and industry contract.
- Materialized entitlement service via Kafka consumer — unnecessary eventual consistency on the hot path.

## Links

- ADR-001 (outbox), ADR-003 (entitlement cache)
