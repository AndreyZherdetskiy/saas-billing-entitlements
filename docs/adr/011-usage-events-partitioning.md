# ADR-011: RANGE partitioning for usage_events

- **Status:** Accepted (stage 2)
- **Date:** 2026-02-12
- **Spec:** §4.3.5 partitioning, §3.4, §11.2

## Context

`usage_events` is a write-heavy append log. Without partitioning, table growth degrades vacuum, period close, and quota scans. LIST by `organization_id` causes hot-tenant skew.

## Decision

1. Parent table `usage_events` with `PARTITION BY RANGE (recorded_at)`.
2. Monthly child tables `usage_events_YYYY_MM` with bounds `[month_start, next_month_start)`.
3. Celery/ops job `ensure_usage_partition` creates the **next** month ahead of time (idempotent).
4. Event idempotency: UNIQUE `(organization_id, idempotency_key)` — **on each partition** + application always writes `recorded_at` in covered range; cross-partition duplicate unlikely with stable client `recorded_at`.
5. **Lookup without `recorded_at`:** dedupe query in `usage.py` filters only by `organization_id` + `idempotency_key` — as partition count grows, multi-partition scan is possible. Ops: rotate/archive old months; clients — stable `recorded_at`. Details: `docs/perf/README.md` § "partition prune ops".
6. Old months: DETACH + archive (runbook); not LIST-shard.
7. Writes only on **primary** (read replica — stage 3).

## Consequences

- Period close and aggregates target the right partitions.
- Monitor "insert fails: no partition".
- Forbidden: unbounded DEFAULT partition as sole strategy forever without alert.

## Alternatives considered

- Unpartitioned heap — simpler S1, does not scale S2 volumes.
- LIST by org — uneven distribution.
- Hash partitioning — worse for time-range period close.

## Links

- ADR-009 (ZDT detach/attach), stage 2 plan Task 16 / 32
