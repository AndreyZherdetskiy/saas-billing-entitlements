# ADR-004: Celery vs outbox-relay boundary

- **Status:** Accepted
- **Date:** 2026-02-10
- **Spec:** §12.3

## Context

Kafka publishing is a critical reliability path: predictable polling, lag metrics, and HA replicas are required. Celery is convenient for batch/cron, but mixing it with the outbox publisher worsens failure modes and backpressure.

## Decision

1. **`outbox-relay`** — separate process/container: sole publisher of domain outbox → Kafka.
2. **Celery** — usage aggregates, period close, grace enforcement, reconciliation cron, dunning steps (stage 2+).
3. Stage 1: worker/Celery is a **stub** in Compose; relay is the working path for ≥5 event types.
4. Forbidden: Celery beat "every N seconds publish outbox" as the primary mechanism.

## Consequences

- One more deployable unit.
- Clean separation: reliability bus vs batch jobs.
- `outbox_lag_seconds` metrics live on relay.

## Alternatives considered

- Celery-only publish — simpler start, worse isolation and backpressure.
- LISTEN/NOTIFY trigger publish — insufficient for scale/replay.

## Links

- ADR-001 (outbox), ADR-008 (dunning S2 uses Celery)
