# ADR-009: Zero-downtime migrations

- **Status:** Accepted
- **Date:** 2026-02-11
- **Spec:** §8.9, §12 (migrations)

## Context

Billing cannot tolerate long DDL locks on hot tables (subscriptions, webhook_events, outbox, ledger). We need disciplined expand/contract.

## Decision

1. Pattern: **expand → dual-write → backfill → switch-read → contract** (separate releases).
2. Do not combine breaking DDL with a deploy that still needs the old schema shape.
3. Forbidden in hot window without offline: enum `ALTER TYPE` with rewrite of large tables; long `CREATE INDEX` without `CONCURRENTLY`; blocking `ALTER COLUMN TYPE`.
4. `alembic upgrade head` on empty DB — **< 60 s** (stage 1 gate).
5. Readiness probe must not fail due to expand migration compatible with old code.

## Consequences

- Longer calendar time for breaking changes; safer for webhook persist.
- Each breaking migration — rollback checklist in PR/task.

## Alternatives considered

- "Big bang" migrate + deploy — risk of lost webhooks/locks.
- Offline maintenance windows only — unacceptable as default.

## Links

- Spec §8.9, Alembic docs expand/contract practices
