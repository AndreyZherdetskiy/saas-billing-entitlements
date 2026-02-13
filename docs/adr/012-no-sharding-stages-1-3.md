# ADR-012: No PostgreSQL sharding in stages 1–3

- **Status:** Accepted
- **Date:** 2026-02-12
- **Spec:** §12.13, §3.5, §11.3

## Context

System design often proposes sharding billing by `organization_id` immediately. That breaks "webhook + outbox + ledger" transactions, complicates reconciliation, and idempotency unique keys.

## Decision

1. Stages **1–3**: single PostgreSQL **primary** for all writes (`subscriptions`, `outbox_messages`, `webhook_events`, ledger, usage ingest).
2. Stage **3**: **read replica** for evaluate (cache miss) / usage reports with acceptable eventual consistency; **RANGE partitions** on `usage_events` by month (ADR-011).
3. **Sharding is not introduced** in stages 1–3 code.
4. Move to shards — roadmap only when **all** criteria below are met.

### Criteria to move to sharding (all required)

1. Primary sustainably saturated on **writes** (WAL/IOPS/CPU), not only SELECT.
2. Partitions + replica + PgBouncer + entitlement cache already deployed and measured.
3. Replica lag grows due to write volume, not heavy reporting.
4. Explicit plan for cross-shard reconciliation and webhook idempotency.

## Consequences

- Simple financial TX preserved.
- Evaluate on replica may be slightly stale — lag threshold + fallback to primary (stage 3).
- Forbidden: add shard routing / multi-primary write in Tasks 34–50.

## Alternatives considered

- Citus / manual shard by org — premature for spec target scale; breaks simple TX.
- Vertical scale primary only — cheaper until threshold, but does not address `usage_events` growth without partitions.

## Links

- Spec §12.13, §3.5, §11.3, §8.1
- ADR-011 (partitions), ADR-002 (Kafka bus), stage 3 plan Task 45
