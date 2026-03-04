# Load testing (§8.1.1)

k6 scenarios for Stage 3 NFR close-out. Spec: `spec.md` §8.1.1 / §10.5.

| Profile | Script | Full intensity | Duration | DoD |
|---------|--------|----------------|----------|-----|
| **A** Evaluate peak | `k6_evaluate_peak.js` | 3,000 RPS evaluate | 10 min | Required (A+C) |
| **B** Usage ingest | `k6_usage_ingest.js` | 1,500 events/s (batch ≤1000) | 10 min | Recommended |
| **C** Mixed | `k6_mixed.js` | 5,000 HTTP RPS mix (3,000 + 1,500 + 500; band 4,500–6,000) | 10 min | Required (A+C) |
| **D** Soak | `k6_soak.js` | 0.3× C: 900 + 450 + 150 | 30–60 min | Recommended |
| **E** Ceiling | `k6_ceiling.js` | Grafana breakpoint: `ramping-arrival-rate` until `abortOnFail`. 8,000 RPS (`K6_CEILING_RPS` default) is a search upper bound, not a hold | until abort (smoke: ≤15 RPS, ~30 s) | Optional (not DoD) |

Reports for A/C smoke: [`profile-a-report.md`](profile-a-report.md), [`profile-c-report.md`](profile-c-report.md). Laptop evaluate hot-path (auth L1 + snapshot L1; canon ADR-003 + ADR-015): [`2026-03-04-hot-path-perf.md`](2026-03-04-hot-path-perf.md) (**last hold 1000 RPS** / **break 1500 RPS** on 1 replica / 4 workers, pool 8+4 `load-*` overlay). Prod-like `make perf-up` hunt (pool 2+1, relay×2): [`2026-03-07-prodlike-hunt.md`](2026-03-07-prodlike-hunt.md) (**last hold 1500 RPS** / **break 2000 RPS**). Pre-L1 SHA-256 baseline on the 2026-03-04 knobs: last hold 400 RPS / break 500 RPS. Full-intensity A/C runs belong on a stand with ≥3 API replicas — laptop = **smoke / characterization only**.

## Locust (additive)

Python Locust smoke reuses the same three HTTP paths as k6 mixed smoke (evaluate / usage ingest / admin usage read). It does **not** replace k6. Profile A DoD (§8.1.1) is **3,000** RPS evaluate on a capable stand with ≥3 API replicas.

| Target | Command | Notes |
|--------|---------|-------|
| Headless smoke | `make load-locust` | Default 5 users / 10s; HTML/CSV under `.local/locust/` |
| Web UI | `make load-locust-ui` | :8089; fails if port busy |
| Locust + Grafana | `make load-locust-otel` | `--otel` → Alloy :4318; needs `make observability-up` |
| k6 + Grafana | `make load-k6-grafana` | Prometheus remote write on compose net; not §8.1.1 DoD |

Install: `uv sync --group load` (includes `locust[otel]`). Credentials: same `K6_*` / `BASE_URL` (or `LOAD_*` overrides). Runbook: [`docs/runbooks/load-locust.md`](../runbooks/load-locust.md). Smoke evidence: [`locust-smoke-report.md`](locust-smoke-report.md). Grafana dashboards (folder **Billing**): **Locust (OTLP)**, **k6 Prometheus** — see [`deploy/observability/README.md`](../../deploy/observability/README.md).

**Prod-like ceiling overlay:** `make perf-up` (tracked `deploy/compose/docker-compose.perf.yml`) — 4 API workers, pool 2+1 on all app processes, rate limits 0, OTEL off, Redis kept, `--scale outbox-relay=2`. Default `make compose-core` stays 1 worker. Overlay is **characterization only** — not profile A 3k RPS DoD. Teardown: `make compose-down` (includes perf file).

**Prod-like multi-org dataset:** seed catalog = single demo tenant; for multi-org / load use [`scripts/seed_prod_like.py`](../../scripts/seed_prod_like.py) (`--profile tiny|medium|full`).

## Kafka / Kafbat expectations

Topic canon (not `*.v1`): `billing.subscription.events`, `billing.invoice.events`, `billing.ledger.events`, `billing.reconciliation.events`, `billing.entitlement.events`, `billing.dlq` — see `deploy/compose/init-kafka-topics.sh`.

- **Profile A** (`k6_evaluate_peak.js`) — evaluate only; **does not** create outbox/Kafka traffic. Empty Kafbat after `make load-a` — expected.
- **Profile B** (usage ingest) — writes usage to PG; **without** Kafka on this path.
- Messages in `billing.*` appear after domain writes + **outbox-relay** (webhooks, subscription lifecycle, ledger, recon, dunning, period close). For UI: `make compose-up` → Kafbat **http://localhost:8081**. For screenshots — **recent** messages after `seed_prod_like` / webhook (not earliest offset; old payloads may contain BIGINT). Read: Kafbat UI or `kafka-console-consumer.sh` in `kafka` container (host `aiokafka` on `localhost:9092` — not supported without advertised listeners fix).

## Seed → k6 env mapping

| Purpose | Env var (repo-root `.env` or export) | Used by |
|---------|--------------------------------------|---------|
| Admin API key | `K6_API_KEY` | k6 Bearer (evaluate + usage + admin read) |
| Demo org | `K6_ORG_ID` | k6 tenant scope |
| Same values | `DEMO_UI_API_KEY` / `DEMO_UI_ORG_ID` | demo-ui (optional) |

`make load-*` runs `_load_env_check`: **fails fast** if `K6_API_KEY` or `K6_ORG_ID` unset. Defaults are in `.env.example` after `cp .env.example .env` (compose demo seed).

## API rate limits vs k6 smoke

Default compose/Helm limits: **120 req/min** (org-scoped keys), **1000 req/min** (`platform_admin`) ≈ **16.7 RPS** per API key. k6 scripts use the seed `platform_admin_key` (`K6_API_KEY`).

| Concern | Mitigation |
|---------|------------|
| Smoke RPS above rate limit → **429** false failures | Smoke profiles target **≤15 RPS** total per key (A/E: 15; C/D mixed: 9+4+2; B: ~1 HTTP RPS). |
| Need higher smoke RPS locally | Set `API_RATE_LIMIT_PER_MINUTE=0` and `API_RATE_LIMIT_PLATFORM_ADMIN_PER_MINUTE=0` — disables Redis rate limiting (`limit <= 0` in code). **Perf/test stands only; never production defaults.** |
| Makefile `load-*` | Recreates `billing-api` with rate limits **0**, `OTEL_SDK_DISABLED=true`, `UVICORN_WORKERS=4`, and a smaller per-process SQLAlchemy pool via a second Compose `--env-file` (`.local/load-perf.env`, later file overrides `.env`; [Compose interpolation](https://docs.docker.com/compose/how-tos/environment-variables/variable-interpolation/)). Uses `up --force-recreate --wait`. Run `make compose-up` first. Restore demo limits with `make compose-core`. |
| Restore prod-like limits | Set `API_RATE_LIMIT_*` back to 120/1000 in `.env` and `docker compose … up -d billing-api`. |

Smoke k6 uses **relaxed thresholds** (not §8.1.1 SLO). `K6_PROFILE=full` keeps strict SLO thresholds for capable stands. For a no-threshold escape hatch: `make load-smoke-all-soft`.

## Prerequisites

1. API reachable — preferably full stack so outbox → Kafka is visible in Kafbat UI:
   ```bash
   make compose-up   # includes kafbat-ui on :8081
   ```
   (`make test-integration` does **not** start compose or kafbat-ui; see README «Kafbat UI».)
2. [k6](https://k6.io/docs/get-started/installation/) installed, **or** omit it — `make load-*` auto-falls back to Docker k6 when `k6` is not on `PATH` (override: `K6=/path/to/k6`).
3. Env in repo-root `.env` (gitignored; Makefile auto-loads it) or exported:
   - `K6_API_KEY` — Bearer (`platform_admin` from seed covers evaluate + usage)
   - `K6_ORG_ID` — organization `public_id` (UUID)
   - `BASE_URL` — default `http://localhost:8000`
   - `K6_PROFILE` — `smoke` (default), `laptop` (profile E overlay breakpoint), or `full` (stand)
   - `K6_FEATURE_KEY` — default `api_calls`

After `cp .env.example .env` (or export from that template):

```bash
export K6_API_KEY=bp_local_demo_platform_admin_key_v1
export K6_ORG_ID=01900000-0000-7000-8000-000000000001
```


## Make targets

Run `make help` for a concise list. From repo root (`.env` with `K6_*` is enough — Makefile auto-loads it):

```bash
# Smoke (laptop-safe) — one profile; rate limits disabled on API via Makefile
make load-a          # or load-b / load-c / load-d / load-e
make load-smoke-all  # A→E sequentially (smoke thresholds; should pass on laptop)

# No k6 thresholds (slow laptop / demo only)
make load-smoke-all-soft

# Ensure compose + disabled rate limits + OTel off on API without running k6
make load-perf-env

# Prod-like ceiling overlay (4 workers, pool 2+1, relay×2; not default compose-core)
make perf-up

# Full §8.1.1 intensity (capable stand only)
make load-a-full     # or load-b-full / … / load-e-full

# Override duration for soak full (default 30m; allowed 30–60m)
make load-d-full K6_SOAK_DURATION=60m
```

Unit/integration suite unchanged: `make test` (lint + mypy + unit + integration).

## Manual k6

```bash
K6_PROFILE=smoke k6 run docs/perf/k6_evaluate_peak.js
K6_PROFILE=smoke k6 run docs/perf/k6_usage_ingest.js
K6_PROFILE=smoke k6 run docs/perf/k6_mixed.js
K6_PROFILE=smoke k6 run docs/perf/k6_soak.js
K6_PROFILE=smoke  k6 run docs/perf/k6_ceiling.js
K6_PROFILE=laptop k6 run docs/perf/k6_ceiling.js  # laptop overlay breakpoint

K6_PROFILE=full k6 run docs/perf/k6_evaluate_peak.js   # stand ≥3 API replicas
```

### Docker k6 (API on host :8000)

`make load-a` … `load-e` use Docker k6 automatically when the host `k6` binary is missing. Explicit docker target (same perf env prep via `_load_perf_rate_limits`):

```bash
make load-a-docker
make load-a-docker LOAD_SCRIPT=k6_mixed.js
```

Manual one-liner (official Docker k6: script on stdin; attach to Compose network so k6 uses service DNS, not host NAT):

```bash
docker run --rm -i --network billing-platform \
  -e K6_API_KEY -e K6_ORG_ID \
  -e BASE_URL=http://billing-api:8000 \
  -e K6_PROFILE=smoke \
  grafana/k6 run - < docs/perf/k6_evaluate_peak.js
```

`make load-*` uses `scripts/run_k6_docker.sh` when host `k6` is missing. Smoke `maxVUs` stays under the API SQLAlchemy pool (`DATABASE_POOL_SIZE` 20 + overflow 10). Laptop evaluate plateaus use `docs/perf/k6_hotpath_plateau.js` with `TARGET_RPS` / `DURATION` / `MAX_VUS` passed through that script (L1 ceiling hunt: 300, 400, 500, 700, 1000… until break). Profile E `K6_PROFILE=laptop` is Grafana breakpoint on this overlay (ramp ~100→2000; not `full`).

## OpenTelemetry (local compose)

Default compose/`OTEL_SDK_DISABLED=true` — SDK **off** (safe for clone + load). Profile `observability` (`make observability-up`) enables OTLP → Alloy → Tempo/Loki/Prometheus + Grafana :3000 — see [`deploy/observability/README.md`](../../deploy/observability/README.md). `make load-*` forces `OTEL_SDK_DISABLED=true` on `billing-api` during runs (parity with rate-limit disable).

Optional load generators → Grafana (visualization only — **not** §8.1.1 DoD):

```bash
make observability-up
make load-locust-otel    # Locust --otel → Alloy :4318 (host); dashboard Locust (OTLP)
make load-k6-grafana     # k6 experimental-prometheus-rw on network billing-platform; dashboard k6 Prometheus
```

Prometheus is **not** published on the host (:9090 stays internal). k6 joins the Compose network and writes to `http://prometheus:9090/api/v1/write`. `load_k6_grafana.sh` feeds the script via `k6 run -` (stdin) because Docker Desktop WSL bind-mounts of `docs/perf` are often empty inside the container. Both Grafana targets fail closed when observability is down (network inspect; Locust OTEL also checks host `:4318`).

Optional load with OTEL (pipeline validation only; not a merge gate):

```bash
LOAD_OTEL_SDK_DISABLED=false OTEL_EXPORTER_OTLP_ENDPOINT=http://alloy:4318 make load-a
```

Local evidence (smoke A, laptop): OTLP export succeeded (no `Failed to export` on API; Tempo search returned `billing-api` evaluate traces). Latency / `dropped_iterations` stayed **PARTIAL** both with OTEL on and off — host capacity dominates; keep default load path OTEL-off.

## Profile notes

- **B:** `K6_BATCH_SIZE` (default 100, max 1000). Full = 1500 events/s → `ceil(1500/batch_size)` HTTP RPS.
- **D:** `K6_SOAK_DURATION` overrides full duration (`30m` default; use `45m` / `60m`). Watch outbox lag outside k6 (runbook / metrics).
- **E:** Grafana [breakpoint](https://grafana.com/docs/k6/latest/testing-guides/test-types/breakpoint-testing/): `ramping-arrival-rate` + `abortOnFail`. `K6_PROFILE=laptop` ramps ~100→2000 with hundreds of VUs (this overlay). `K6_CEILING_RPS` (default 8000) is the **full** ramp target, not a constant hold — **stand only**. Smoke stays ≤15 RPS (CI-safe). Document the abort (or ramp-end) progress-line `iters/s`, not whole-test `http_reqs`; not a merge gate.
- **Outbox lag (C/D):** not asserted inside k6; check `outbox_lag_seconds` / runbook during/after the run.

## Usage idempotency lookup (partition prune ops)

`ingest_usage_batch` dedupes by `(organization_id, idempotency_key)` **without** `recorded_at` filter — PostgreSQL may scan **all** monthly `usage_events` partitions on lookup (ADR-011). Ops expectation:

- Clients must send stable `recorded_at` (or accept server `now()` on ingest).
- Retention: `DETACH` old months per runbook reduces scan cost.
- Future optimization: time-window predicate on lookup — roadmap; not a smoke/DoD blocker.

See [ADR-011](../adr/011-usage-events-partitioning.md) and runbook partition ensure (`create_usage_partition`).

## Smoke maxVUs and dropped iterations

Smoke scripts target **≤15 RPS** per key; `make load-*` disables API rate limits (P1). If k6 still reports high `dropped_iterations`:

1. Prefer `make load-smoke-all` (not raw `k6 run` without `_load_perf_rate_limits`).
2. Compose default and `make load-*` keep `OTEL_SDK_DISABLED=true` on API (Console exporter is sync).
3. Escape hatch: `make load-smoke-all-soft` (`--no-thresholds`).
4. On very slow hosts, lower smoke RPS in script `smoke` profile or accept PARTIAL evidence (§10.5).

Profile A/C reports (2026-03-04) predate rate-limit fix — see notes in those reports.

## Forbidden (spec)

- Claiming Stage 3 load DoD with Stage 2 numbers (1k evaluate/s).
- Calling 100k+ RPS on a laptop “prod validation”.
