# Runbook: Replica lag

**Status:** Task 36 operational (topology + config); evaluate RO routing — Task 37
**Alert / symptom:** `replica_lag_seconds` above `REPLICA_LAG_THRESHOLD_SECONDS`; stale evaluate / usage reports

## Symptoms

- [ ] Metric `replica_lag_seconds` (or manual check) exceeds Settings threshold (`REPLICA_LAG_THRESHOLD_SECONDS`, default 30s)
- [ ] Evaluate after cache miss returns stale entitlements (subscription changed on primary, client sees old state)
- [ ] Admin usage reports diverge from primary by seconds / minutes
- [ ] App logs (Task 37+) — fallback to primary due to lag
- [ ] `pg_stat_replication` on primary: `replay_lag` / `write_lag` growing on replica

## Quick checks

- [ ] Primary healthy: `pg_isready` / `/health/ready` (DB check on primary DSN)
- [ ] Replica in standby and streaming:
  ```sql
  -- on primary
  SELECT application_name, state, sync_state,
         write_lag, flush_lag, replay_lag
  FROM pg_stat_replication;
  ```
- [ ] On replica: `SELECT pg_is_in_recovery();` → `t`
- [ ] Lag in seconds (approximate):
  ```sql
  SELECT EXTRACT(EPOCH FROM (now() - pg_last_xact_replay_timestamp())) AS replica_lag_seconds;
  ```
  (`NULL` right after promote / empty cluster — do not treat as 0.)
- [ ] `DATABASE_READ_URL` points to replica; `DATABASE_URL` — primary only (direct or via PgBouncer — see [pgbouncer-pools.md](pgbouncer-pools.md))
- [ ] No INSERT/UPDATE/DELETE via RO DSN (app and ad-hoc SQL)

## Safe actions

- [ ] **Fallback to primary for reads:** clear `DATABASE_READ_URL` or raise threshold temporarily only after assessing stale-read risk; restart affected API/worker pods/containers
- [ ] Ensure evaluate / reports do not write to replica (Gate A: all mutations — `DATABASE_URL`)
- [ ] Check primary load (long transactions, vacuum, bulk load) — common lag cause
- [ ] Restart replica **only** if streaming broken (`state != streaming`): `docker compose -p billing-platform -f deploy/compose/docker-compose.yml --profile postgres-replica restart postgres-replica` (local); in K8s — per cluster runbook
- [ ] Monitor until lag below threshold; then restore RO DSN

## Do not

- [ ] Failover writes to replica without separate DR runbook (spec §8.1 — replica does not replace primary for RW)
- [ ] INSERT/UPDATE/DELETE on RO DSN “to offload” primary
- [ ] Dual-write between primary and replica
- [ ] Ignore growing lag with `DATABASE_READ_URL` enabled without fallback (Task 37 should fall back to primary)

## Escalation

| Severity | Condition | Action |
|----------|-----------|--------|
| P3 | lag > threshold, evaluate degraded, primary OK | on-call: fallback RO → primary, monitor |
| P2 | lag > 5 min or replica disconnected | eng + DBA; check WAL/disk/network |
| P1 | primary down | primary incident runbook; RO read-only snapshot only, no writes |

## Local Compose (profile `postgres-replica`)

```bash
docker compose -p billing-platform -f deploy/compose/docker-compose.yml --profile postgres-replica up -d
# repo-root .env:
# DATABASE_READ_URL=postgresql+asyncpg://billing:billing@postgres-replica:5432/billing
# REPLICA_LAG_THRESHOLD_SECONDS=30
```

Role `replicator` is created on **first** primary volume init. If volume is old — recreate `postgres-data` or create role manually.

`pg_hba` rule for streaming replication (`host replication replicator …`) is mounted on primary via `hba_file` (`deploy/compose/postgres-replica/pg_hba.conf`) and applies on every start. Password `replicator` — placeholder for local dev only; in prod use separate secrets and narrow CIDR.

## Related documents

- ADR-003 amendment (replica read path), ADR-012 (no sharding)
- `spec.md` §8.1 routing; stage 3 plan Tasks 36–37
- `docs/slo.md` — lag alerts (when configured)
