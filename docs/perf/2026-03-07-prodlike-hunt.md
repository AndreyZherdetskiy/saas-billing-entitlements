# Prod-like overlay hunt — `make perf-up` (2026-03-07)

**Spec:** §8.1 / §8.1.1, ADR-015 (auth L1), ADR-003 (snapshot L1), ADR-004 (relay HA)
**Plan:** [`docs/plans/2026-03-08-prodlike-perf.md`](../plans/2026-03-08-prodlike-perf.md) Task 2
**Method:** same k6 plateau contract as [`2026-03-04-hot-path-perf.md`](2026-03-04-hot-path-perf.md); **numbers in this file are from this run only**. Overlay knobs differ (pool **2+1**, `outbox-relay` ×2). Not profile A DoD.

**Headline:** last **hold 1500 RPS** evaluate (0% fail, 0 dropped, 22 s); **break 2000 RPS** (`dropped_iterations` 2822, achieved 1824 /s, p50 89 ms, max VUs 800). Primary limiter: **SUT evaluate-path latency at 1 replica / 4 workers** (CPU peg unproven on WSL `docker stats`) — do not `--scale billing-api`.

## Overlay (inspected after `make perf-up`, before load)

`make compose-down` then `make perf-up` (`--build --wait --scale outbox-relay=2`). Frozen ports not remapped. No other numbered `_real_projects` compose stack held ports.

| Knob | Inspected value |
|------|-----------------|
| `billing-api` replicas | **1** (`billing-platform-billing-api-1`) |
| `billing-worker` / `billing-beat` | **1** / **1** |
| `outbox-relay` | **2** (`…-outbox-relay-1`, `…-outbox-relay-2`) |
| API Cmd | `uvicorn billing_platform.main:app --host 0.0.0.0 --port 8000 --workers 4 --timeout-graceful-shutdown 30` |
| `UVICORN_WORKERS` | **4** |
| `DATABASE_POOL_SIZE` / `DATABASE_MAX_OVERFLOW` | **2** / **1** |
| `API_RATE_LIMIT_PER_MINUTE` | **0** |
| `OTEL_SDK_DISABLED` | **true** |
| `REDIS_URL` | **non-empty** (not cleared) |
| `GET /health/ready` | HTTP **200** (`postgres`/`redis`/`kafka` ok) |
| Postgres `max_connections` | **100** (Clock B) |
| Host | WSL2 Linux x86_64, 16 CPUs, ~16 GiB RAM |

`docker top` on `billing-api` showed four uvicorn worker processes for the whole hunt. Overlay env and Cmd were still pool 2+1 / `--workers 4` after k6 and Locust (generators did **not** recreate `billing-api`).

Did **not** run `make load-locust` / `make load-a` / `_load_perf_rate_limits` (those recreate API with pool 8+4 and drop this overlay).

## Commands (no secrets)

Credentials: `set -a && source .env && set +a` in the shell. Scripts do not source `.env`.

```bash
make compose-down || true
make perf-up
curl -sf http://localhost:8000/health/ready

TARGET_RPS=700  DURATION=22s MAX_VUS=800 ./scripts/run_k6_docker.sh k6_hotpath_plateau.js
TARGET_RPS=1000 DURATION=22s MAX_VUS=800 ./scripts/run_k6_docker.sh k6_hotpath_plateau.js
TARGET_RPS=1500 DURATION=22s MAX_VUS=800 ./scripts/run_k6_docker.sh k6_hotpath_plateau.js
# 1500 still met hold (0% fail, 0 dropped, achieved ≈ target); one extra plateau to name break:
TARGET_RPS=2000 DURATION=22s MAX_VUS=800 ./scripts/run_k6_docker.sh k6_hotpath_plateau.js

LOAD_WAIT_MIN=0 LOAD_WAIT_MAX=0 LOAD_USERS=20 LOAD_SPAWN_RATE=10 LOAD_RUN_TIME=30s \
  ./scripts/load_locust_smoke.sh
LOAD_WAIT_MIN=0 LOAD_WAIT_MAX=0 LOAD_USERS=40 LOAD_SPAWN_RATE=10 LOAD_RUN_TIME=30s \
  ./scripts/load_locust_smoke.sh
```

`load_wait_bounds()` with those env vars printed `(0.0, 0.0)` before Locust started (`wait_time` is import-time). Did not run `K6_PROFILE=laptop` (plateaus already named hold vs break). Did not run Locust 50 users (40 already hit stop: p50 ×2, RPS flat, Locust CPU >90%).

k6: Docker stdin, compose network `billing-platform`, `BASE_URL=http://billing-api:8000`. No `--no-thresholds`. Script thresholds on plateaus are `http_req_failed rate<0.5` and `dropped_iterations count<10000` (loose characterization). `rate<0.05` = 5% applies to `k6_ceiling.js`, not these plateaus.

## Idle (host `:8000`)

| Call | HTTP | Time | Notes |
|------|------|------|--------|
| `GET /health/ready` | 200 | **6.9 ms** | |
| `POST /v1/entitlements/evaluate` | 200 | **50.44 ms** | `cache_hit: false` |
| `POST /v1/entitlements/evaluate` | 200 | **2.07 ms** | `cache_hit: true` |

## Clock A — k6 evaluate plateaus (`constant-arrival-rate`, 22 s)

Script: [`k6_hotpath_plateau.js`](k6_hotpath_plateau.js). Warmup evaluate in `setup()`. `MAX_VUS=800`. Scheduled arrival ≠ achieved RPS. Hold = target ≈ achieved, 0% fail, 0 dropped. Break = first plateau that is not a hold (here: dropped storm + achieved short + p50 climb; fail stayed 0%).

| Target RPS | Achieved (`http_reqs`) | fail % | dropped | p50 (`med`) | p99 | VUs max | Verdict |
|------------|------------------------|--------|---------|-------------|-----|---------|---------|
| 700 | 700.98 /s | 0.00% | 0 | 1.75 ms | 72.37 ms | 11 / 800 | hold |
| 1000 | 996.63 /s | 0.00% | 0 | 2.39 ms | 92.82 ms | 98 / 800 | hold (last ~2.4 ms p50) |
| 1500 | 1478.91 /s | 0.00% | 0 | 13.09 ms | 745.51 ms | 465 / 800 | **last hold** (p50 already up vs 1000) |
| 2000 | 1823.77 /s | 0.00% | 2822 | 88.96 ms | 3.90 s | 800 / 800 | **break** |

2000: k6 warning `Insufficient VUs, reached 800 active VUs`. Later `dropped_iterations` with p50 climb = SUT latency ([Grafana dropped_iterations](https://grafana.com/docs/k6/latest/using-k6/scenarios/concepts/dropped-iterations/)), not VU starvation at ~2 ms. Checks 100% status 200 on all four plateaus.

API logs for this container over the hunt: **0** `IndexError`, **0** `MaxConnectionsError`, **0** `TooManyConnections`, **0** HTTP 5xx access lines. `/health/ready` stayed 200 after plateaus and Locust.

## Clock A — mixed Locust `wait_time=0`

Weights: Evaluate 9 / Usage 4 / Admin 2. Host `http://localhost:8000`. Console totals at shutdown (CSV is a few hundred requests lower).

### 20 users / 30 s (mixed hold)

Spawn: `EvaluateUser` 12, `UsageIngestUser` 5, `AdminReadUser` 3. Locust warned **CPU usage above 90%** (generator process).

| Name | # reqs | fail % | RPS | p50 | p99 |
|------|--------|--------|-----|-----|-----|
| `POST /v1/entitlements/evaluate` | 23341 | 0.00% | 786.87 | 11 ms | 44 ms |
| `POST /v1/usage/events/batch` | 2692 | 0.00% | 90.75 | 47 ms | 130 ms |
| `GET /v1/organizations/{org}/usage` | 3233 | 0.00% | 108.99 | 20 ms | 68 ms |
| Aggregated | 29266 | 0.00% | 986.61 | 12 ms | 84 ms |

### 40 users / 30 s (stop)

| Name | # reqs | fail % | RPS | p50 | p99 |
|------|--------|--------|-----|-----|-----|
| `POST /v1/entitlements/evaluate` | 24564 | 0.00% | 823.96 | 20 ms | 90 ms |
| `POST /v1/usage/events/batch` | 2464 | 0.00% | 82.65 | 110 ms | 270 ms |
| `GET /v1/organizations/{org}/usage` | 3258 | 0.00% | 109.28 | 31 ms | 140 ms |
| Aggregated | 30286 | 0.00% | 1015.90 | 22 ms | 180 ms |

Stop: evaluate p50 11→20 ms and usage p50 47→110 ms (~×2) while aggregated RPS stayed ~1000 /s (did not scale with users). Locust CPU >90% again. Fail 0% — not 5xx. Usage named stats dominate mixed latency; evaluate stays cheaper.

## Clock B — mid-run samples

`max_connections=100`, `pg_stat_activity` count, unpublished outbox, `docker stats --no-stream` (primed except the first 700 sample). Usage ingest does not write outbox; unpublished=0 is expected on this mix and was recorded anyway.

| When | `max_connections` | `pg_stat_activity` | unpublished | `billing-api` CPU | postgres | redis | kafka | outbox-relay ×2 | billing-worker |
|------|-------------------|--------------------|-------------|-------------------|----------|-------|-------|-----------------|----------------|
| k6 700 (~10 s) | 100 | 14 | 0 | 0.85% (first `docker stats`; often ~0) | 6.22% | 2.55% | 3.16% | 2.92% / 3.83% | 0.23% |
| k6 1000 (primed) | 100 | 14 | 0 | 0.82% / 0.88% | 6.14% / 1.59% | 0.20% / 2.96% | 1.83% / 179.36% | ~3–4% | 0.13% / 65.38% |
| k6 1500 (primed) | 100 | 14 | 0 | 0.86% / 0.99% | 1.83% / 1.34% | 0.21% / 0.23% | 2.19% / 2.88% | ~3% | 0.20% / 0.04% |
| Locust 20 mixed | 100 | 14 | 0 | **25.16%** | 1.43% | 2.52% | 1.90% | 2.97% / 2.89% | 0.05% |
| Locust 40 mixed | 100 | 14 (1 active / 8 idle) | 0 | 0.81% / 0.77% | 6.21% / 6.11% | 0.16% / 0.24% | 1.73% / 2.21% | ~3% | 0.03% / 0.17% |

`docker top` worker lifetime `%CPU` late in the hunt (four spawn workers): ~11–16% each (includes idle time since `perf-up`). WSL2 `docker stats` for `billing-api` did **not** show 4 cores pegged during k6 plateaus; one Locust-20 sample showed 25%. Kafka 179% / worker 65% on one 1000 sample is not the evaluate path (evaluate does not publish Kafka). Relay ~3% with unpublished=0 is **not** a relay/publish backlog.

## Limiter (one)

| Candidate | This run |
|-----------|----------|
| HTTP 500 `TooManyConnections` / activity at cap | **No.** activity 14 / cap 100 on every sample. |
| Pool / `TimeoutError` / `IndexError: pop from empty list` / `MaxConnectionsError` **on hold** | **No.** 0% fail on 700–2000 plateaus and both Locust runs; 0 matching API log lines. |
| Unpublished rising, relay idle | **No.** unpublished=0; relay ~3% CPU. |
| Usage named stats dominate fail/p50; evaluate still cheap | Mixed Locust only: usage p50 higher, **0 fail**. k6 evaluate-only still broke at 2000 without usage. Not the evaluate ceiling. |
| `wait_time` not 0 | **No.** `LOAD_WAIT_MIN/MAX=0` before process start; 20 users at ~987 RPS is incompatible with default 0.1–0.5 s wait. |
| Locust generator CPU | **Yes as mixed-clock stop**, not as evaluate ceiling. 20→40 users: RPS flat, Locust CPU >90%. |
| API 4 cores pegged (`docker stats`) | **No.** k6 holds ~0.7–1%; Locust-20 peak 25%. Not the brief API-CPU row. |
| Evaluate latency + dropped; PG/Redis idle | **Yes — primary.** Break 2000: 0% HTTP fail, dropped 2822, p50 89 ms, max VUs; PG/Redis not the wall. |

**Primary limiter: SUT evaluate-path latency at 1 replica / 4 workers (CPU peg unproven on WSL `docker stats`).** Task 3 must **not** treat this as a proven software defect — no pool timeout, no Redis pool, no relay loop, no replica-hammer. Do **not** `--scale billing-api`, raise `max_connections`, or clear `REDIS_URL`. Mixed Locust wait=0 is additionally capped by the Locust process CPU (separate from the evaluate ceiling).

## Task 3 — software fix (2026-03-07)

**Verdict:** No `src/` change (first brief table row — overlay did not prove a software defect).

Task 2 named evaluate-path latency at 1 replica / 4 workers, but hold plateaus had **0% fail**, **0** dropped iterations, `pg_stat_activity` **14 / 100** on every Clock B sample, **unpublished = 0**, and **no** pool `TimeoutError` / `TooManyConnections` / Redis `MaxConnectionsError` / `IndexError: pop from empty list` / HTTP 5xx on hold. Break at 2000 RPS was k6 `dropped_iterations` with 0% HTTP fail — not a proven pool, Redis, relay, or usage-ingest software bug.

No pool-timeout 503, Redis `max_connections`, usage-ingest trim, or persistent relay loop. No `--scale`, worker bump, or `max_connections` raise. Proceed to Task 4 remeasure (overlay knobs may still change numbers).

## Tear-down

```bash
make compose-down
docker compose -p billing-platform ps -a   # empty
```

`make compose-down` included `docker-compose.perf.yml`, no `-v`. After down: no `billing-platform` containers, network removed.

## Scope notes

- These measurements are the **`make perf-up` overlay** (pool 2+1, relay ×2, 2026-03-07), not the 2026-03-04 `make load-*` overlay (pool 8+4, relay ×1).
- Not §8.1.1 profile A (3,000 RPS / ≥3 replicas / 10 min).
- 1500 RPS on **this** overlay met hold (0 dropped); 2026-03-04 break-at-1500 is a different overlay and is not restated as this run.
