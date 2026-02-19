# Runbook: Zero-downtime migration drill (hot table)

> **Stage 2 Task 32** — expand-only drill per ADR-009 / spec §8.9. Contract step **deferred**.

**Status:** Stage 2 operational drill
**Related ADRs:** ADR-009 (expand/contract), ADR-011 (usage partitions)
**Migration:** `alembic/versions/20260216_0017_zdt_drill_invoices.py`

## Why `invoices`, not `usage_events` partition

The plan names this runbook `migration-zdt-usage` because the alternative hot path is **online ensure** of the next monthly partition for `usage_events` (ADR-011, Task 16). For the stage 2 drill we chose a **narrower expand-only path**:

| Criterion | `invoices` + nullable column | `usage_events` + partition |
|-----------|------------------------------|----------------------------|
| DDL in Alembic | `ADD COLUMN` nullable | `CREATE TABLE ... PARTITION OF` (or worker `ensure_usage_partition`) |
| Old code compatibility | Yes — column not read by app | Yes — partition already covered by Task 16 |
| Drill isolation | Separate revision without ingest impact | Overlaps prod partition ensure path |
| Expand rollback | `DROP COLUMN` in downgrade | `DROP TABLE` partition (careful with data) |

**Decision:** drill = `invoices.zdt_drill_marker` (nullable `VARCHAR(64)`). Partition ensure stays in [`create_usage_partition`](../../src/billing_platform/workers/tasks/create_usage_partition.py) and Task 16 tests; this runbook documents the **same** expand→migrate→contract cycle on invoices.

## Expand → migrate → contract (contract deferred)

| Phase | Release | Action | Stage 2 status |
|-------|---------|--------|----------------|
| **1. Expand** | Task 32 | `ADD COLUMN zdt_drill_marker NULL` on `invoices` | **Done** (`20260216_0017`) |
| **2. Dual-write** | future | Code writes marker on sync/recon (if needed) | Deferred |
| **3. Backfill** | future | Batched UPDATE for historical rows | Deferred |
| **4. Switch-read** | future | Read marker in API/admin | Deferred |
| **5. Contract** | future | `DROP COLUMN zdt_drill_marker` | **Deferred** — not in same release as expand |

**ADR-009 invariant:** do not deploy code that **requires** the new column in the same release as expand. Current release — DDL expand only; ORM/application unchanged.

## Locks (no exclusive lock assumption)

PostgreSQL 11+ for `ADD COLUMN ... NULL` without `DEFAULT`:

- Does not rewrite the whole table.
- Brief `ACCESS EXCLUSIVE` on relation metadata (usually milliseconds at typical volumes).
- Existing `SELECT`/`INSERT`/`UPDATE` on `invoices` continue; new rows get `NULL` in `zdt_drill_marker`.

**Do not run in hot window without offline:**

- `ALTER TYPE` enum with rewrite
- `CREATE INDEX` without `CONCURRENTLY` on large `invoices`
- `ALTER COLUMN TYPE` / `SET NOT NULL` without backfill phase

Integration test `tests/integration/test_zdt_migration_expand.py` asserts: upgrade adds column, concurrent read on `invoices` does not fail, expand-step downgrade is reversible.

## Apply procedure (ops / local)

1. **Pre-check**
   - [ ] `alembic current` — head-1 = `20260216_0016`
   - [ ] No incomplete deploy requiring contract
   - [ ] Readiness probe: expand does not break old code (column not in app SELECT)

2. **Expand**
   ```bash
   uv run alembic upgrade 20260216_0017
   # or upgrade head if 0017 is latest revision
   ```

3. **Verify**
   ```sql
   SELECT column_name, is_nullable, data_type
   FROM information_schema.columns
   WHERE table_name = 'invoices' AND column_name = 'zdt_drill_marker';
   -- expected: nullable, character varying
   ```

4. **Rollback expand only** (if rollback needed before dual-write)
   ```bash
   uv run alembic downgrade 20260216_0016
   ```
   Safe only while **no** service writes to `zdt_drill_marker`.

## Problem symptoms

- [ ] `alembic upgrade` hangs > 60 s on empty DB (ADR-009 gate p.4 violation)
- [ ] Deploy after expand: app crashes on `UndefinedColumn` — expand-only violated (code deployed before migration or vice versa with contract)
- [ ] Webhook/invoice path latency spike during DDL — check lock wait (`pg_locks`, `pg_stat_activity`)

## Safe actions

- [ ] Apply expand in low-traffic window (discipline, not hard requirement for nullable ADD)
- [ ] Monitor `pg_stat_activity.wait_event_type = 'Lock'` on `invoices`
- [ ] Roll back expand revision only (`downgrade 20260216_0016`) if column unused by code

## Do not

- [ ] `SET NOT NULL` / index on `zdt_drill_marker` in same PR as expand
- [ ] Drop column (contract) while dual-write or code reads exist
- [ ] Mix drill with breaking enum/type change on `invoices`

## Related documents

- [`docs/adr/009-zero-downtime-migrations.md`](../adr/009-zero-downtime-migrations.md)
- [`docs/adr/011-usage-events-partitioning.md`](../adr/011-usage-events-partitioning.md)
- Stage 2 plan Task 32, Task 16 (usage partitions)
- `tests/integration/test_zdt_migration_expand.py`
