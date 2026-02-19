# Runbook: PgBouncer and connection pools

**Status:** Task 38 operational (Compose profile + Helm values stub)
**Symptom / context:** rising PG backends; `too many connections`; stage 3 pooling setup

## Architecture (stage 3)

```text
API / worker / relay  →  PgBouncer (session pool)  →  PostgreSQL primary (RW)
evaluate / reports    →  replica DSN (direct or optional pgbouncer-replica)  →  standby (RO)
```

- **All mutations** — only via `DATABASE_URL` on primary (through bouncer or direct).
- `DATABASE_READ_URL` — optional for evaluate/reports (Task 37); not for writes.
- Sharding out of scope (ADR-012).

## Recommended pool sizes

| Layer | Parameter | Value (default) | Env / config |
|-------|-----------|-----------------|--------------|
| SQLAlchemy (per process) | `pool_size` | 20 | `DATABASE_POOL_SIZE` |
| SQLAlchemy (per process) | `max_overflow` | 10 | `DATABASE_MAX_OVERFLOW` |
| PgBouncer | `default_pool_size` | 25 (primary) | `deploy/compose/pgbouncer/pgbouncer.ini` |
| PgBouncer | `max_client_conn` | 200 (primary) | same |
| PgBouncer RO (opt.) | `default_pool_size` | 15 | `pgbouncer-replica.ini` |
| PgBouncer | `pool_mode` | `session` | async SQLAlchemy + asyncpg |

**Rule:** sum of `(pool_size + max_overflow) × replicas` API/worker/relay ≤ bouncer `max_client_conn`; bouncer `default_pool_size` ≤ PostgreSQL `max_connections` with headroom for admin/replication.

NFR source: `spec.md` §8.1 (`pool_size=20`, `max_overflow=10` per API instance).

## Local Compose

### Primary via PgBouncer

**Important:** `pgbouncer` profile does **not** rewrite `DATABASE_URL` automatically. Default compose keeps DSN on `postgres:5432`. After `--profile pgbouncer` **manually** set DSN and restart API/worker/relay — otherwise traffic bypasses bouncer.

```bash
docker compose -p billing-platform -f deploy/compose/docker-compose.yml --profile pgbouncer up -d
```

In repo-root `.env`:

```env
# In-compose (service DNS). Host publish for tools: localhost:6432 → container 5432.
DATABASE_URL=postgresql+asyncpg://billing:billing@pgbouncer:5432/billing
DATABASE_POOL_SIZE=20
DATABASE_MAX_OVERFLOW=10
```

Restart `billing-api`, `billing-worker`, `outbox-relay` after DSN change.

### Replica + optional RO bouncer

```bash
docker compose -p billing-platform -f deploy/compose/docker-compose.yml \
  --profile postgres-replica --profile pgbouncer-replica up -d
```

```env
# In-compose. Host publish: localhost:6433 → container 5432.
DATABASE_READ_URL=postgresql+asyncpg://billing:billing@pgbouncer-replica:5432/billing
```

Without RO bouncer — direct DSN to `postgres-replica:5432` (see [replica-lag.md](replica-lag.md)).

## Helm (stub)

`deploy/helm/billing-platform/values.yaml` → `pgbouncer.enabled: false` (placeholder). When enabled in cluster: external PgBouncer Deployment or subchart; `secrets.DATABASE_URL` points to bouncer Service, not PG pod directly.

## Quick checks

- [ ] `docker compose -p billing-platform -f deploy/compose/docker-compose.yml --profile pgbouncer config` — `pgbouncer` service present, port `6432`
- [ ] Admin console (local): `psql -h localhost -p 6432 -U billing pgbouncer` → `SHOW POOLS;`
- [ ] `/health/ready` API OK with DSN through bouncer
- [ ] `pg_stat_activity` on primary: backend count ≈ `default_pool_size`, not hundreds per pod

## Do not

- [ ] `pool_mode = transaction` without disabling asyncpg prepared statements (breaks ORM)
- [ ] Route INSERT/UPDATE/DELETE to RO DSN or replica bouncer “to offload”
- [ ] Commit real passwords (local demo: generate PgBouncer userlist from `.env` at start)

## Related documents

- [replica-lag.md](replica-lag.md) — RO routing and fallback
- `spec.md` §3.5, §8.1; stage 3 plan Task 38
- ADR-003, ADR-012
