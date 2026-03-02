# Runbook: Local Compose profiles

**Context:** optional profiles in `deploy/compose/docker-compose.yml` (project name `billing-platform`).
**Entry points:** `make compose-core` | `make compose-all` | `make observability-up` — see `Makefile` `help`.

## Profiles

| Profile | Services | Notes |
|---------|----------|--------|
| _(default)_ | core app (PG primary, Redis, Kafka, API, worker, beat, relay, mock-stripe, kafbat-ui, demo-ui) | `make compose-core` (1 Uvicorn worker) |
| `perf overlay` | `deploy/compose/docker-compose.perf.yml` — opt-in via `make perf-up` | 4 API workers, pool 2+1, rate limit 0, OTEL off, Redis kept, `--scale outbox-relay=2` |
| `postgres-replica` | `postgres-replica` | RO DSN — see [replica-lag.md](replica-lag.md) |
| `pgbouncer` / `stage3` | `pgbouncer` | Does **not** rewrite `DATABASE_URL` — [pgbouncer-pools.md](pgbouncer-pools.md) |
| `pgbouncer-replica` | `pgbouncer-replica` | Needs `postgres-replica` |
| `observability` | LGTP (Alloy, Prometheus, Grafana, Loki, Tempo) | `make observability-up`; also `docs/perf/README.md` for OTEL |

## Full stack

```bash
make compose-all   # core + replica + both bouncers + observability
```

On first boot, `billing-api` runs migrations + deterministic demo seed (catalog, demo org, fixed local API key). Defaults are in `.env.example` / Compose `demo-ui` env. Additive multi-tenant data: `scripts/seed_prod_like.py`.

Set `DATABASE_READ_URL` in repo-root `.env` if evaluate/reports should use the replica; recreate `billing-api` after changing DSN.

## Perf overlay (ceiling characterization)

`make perf-up` applies `deploy/compose/docker-compose.perf.yml` on top of core. **Not** the default `compose-core` path.

| Knob | Value | Notes |
|------|-------|-------|
| API workers | 4 | Uvicorn `--workers 4` on `billing-api` |
| SQLAlchemy pool | 2 + 1 overflow | `billing-api`, `billing-worker`, `billing-beat`, `outbox-relay` |
| Rate limits | 0 | `API_RATE_LIMIT_*=0` (Redis rate limiter disabled) |
| OTEL | off | `OTEL_SDK_DISABLED=true` |
| Redis | inherited | `REDIS_URL` kept from `x-app-env` (evaluate cache) |
| Relay scale | 2 | `--scale outbox-relay=2` only (never scale `billing-api` or `billing-worker`) |

`make compose-down` includes the perf overlay file (volumes kept). `make load-locust` does **not** use the overlay — it uses `_load_perf_rate_limits` on the default core stack.

## Replica caveats

- Replicator role is created on primary **first init only** — recreate `postgres-data` if missing.
- Primary `pg_hba` (replication rule) is copied into `local/billing-platform-postgres:16` (`hba_file=/etc/postgresql/pg_hba.conf`). Local placeholder password.

## Docker Desktop + WSL2

Stock `make compose-core` does **not** bind-mount init scripts: Postgres and kafka-init files are baked into images; `.dockerignore` excludes `demo_ui/node_modules`.

Optional profiles still bind-mount host files (`postgres-replica` entrypoint, PgBouncer ini, observability configs). Those can fail with `ubuntu.sock` / distro mount errors on some Docker Desktop + WSL2 hosts — `make compose-all` / `make observability-up` are not the core path.

## Related

- [pgbouncer-pools.md](pgbouncer-pools.md)
- [replica-lag.md](replica-lag.md)
- `deploy/observability/README.md`
- Spec / stage mapping: `.superpowers/sdd/progress.md` (gitignored local harness)
