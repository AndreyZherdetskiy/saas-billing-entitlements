# Evaluate hot-path — laptop overlay (2026-03-04)

**Spec:** §8.1 / §8.1.1, ADR-015 (SHA-256 API-key lookup + auth L1), ADR-003 (snapshot key + snapshot L1)
**Plans:** [`docs/plans/2026-02-26-evaluate-hit-path.md`](../plans/2026-02-26-evaluate-hit-path.md) Task 3; prior SHA-256 work [`docs/plans/2026-03-04-evaluate-hot-path-perf.md`](../plans/2026-03-04-evaluate-hot-path-perf.md) Tasks 4–5
**Scope:** laptop **capacity characterization** (1 API replica / 4 workers). §8.1.1 profile A DoD: ≥3 API replicas on a capable stand.

**Current (auth L1 + snapshot L1 in the running image):** last **hold 1000 RPS**, **break 1500 RPS**. Pre-L1 SHA-256 overlay on the same knobs: last hold **400 RPS**, break **500 RPS** (section below). Numbers are from **this** overlay on a developer WSL2 laptop. Stage 3 §8.1 cached evaluate (**3,000** RPS) is the **scaled target** (≥3 API replicas × the 1000 RPS / 4-worker hold). Profile A DoD: **3,000** RPS / 10 min / p99 < 50 ms, ≥3 API replicas.

## Overlay (what `make load-*` actually set)

| Knob | Value |
|------|--------|
| API replicas | **1** (`billing-api`) |
| `UVICORN_WORKERS` | **4** |
| SQLAlchemy `DATABASE_POOL_SIZE` / `DATABASE_MAX_OVERFLOW` | **8 / 4** (per process) |
| `API_RATE_LIMIT_PER_MINUTE` / platform-admin | **0 / 0** (disabled) |
| `OTEL_SDK_DISABLED` | **true** |
| Auth L1 TTL / snapshot L1 TTL | **2 s / 1 s** (Settings defaults; `hotpath_cache` present in the image) |
| Postgres / Redis / Kafka | compose-core (1 primary, 1 Redis, 1 Kafka) |
| k6 | `grafana/k6` via [`scripts/run_k6_docker.sh`](../../scripts/run_k6_docker.sh) (stdin, network `billing-platform`, `BASE_URL=http://billing-api:8000`) |
| Host | WSL2 Linux x86_64, 16 CPUs, ~16 GiB RAM |

Rebuild: `make compose-core` then `make _load_perf_rate_limits` (writes `.local/load-perf.env`, `up --no-deps --force-recreate --wait billing-api`). Image: `hash_api_key` digest length 64 (not bcrypt `$2`); process L1 module loaded; `auth_cache_ttl_seconds=2`, `entitlement_l1_ttl_seconds=1`. Ports were not remapped. Overlay was recreated before L1 plateaus, before the confirm pair, and before the laptop breakpoint.

## Commands (no secrets)

Rebuild + overlay:

```bash
make compose-core
make _load_perf_rate_limits
curl -sf http://localhost:8000/health/ready
```

Idle evaluate (host `:8000`; invalidate first so the first POST is a miss). Credentials from repo-root `.env` (`K6_API_KEY` / `K6_ORG_ID`) — **not** repeated here.

k6 plateaus (Compose network, stdin script). L1 ceiling hunt started at 300 RPS with `MAX_VUS=800`:

```bash
TARGET_RPS=300  DURATION=22s MAX_VUS=800 ./scripts/run_k6_docker.sh k6_hotpath_plateau.js
TARGET_RPS=400  DURATION=22s MAX_VUS=800 ./scripts/run_k6_docker.sh k6_hotpath_plateau.js
TARGET_RPS=500  DURATION=22s MAX_VUS=800 ./scripts/run_k6_docker.sh k6_hotpath_plateau.js
TARGET_RPS=700  DURATION=22s MAX_VUS=800 ./scripts/run_k6_docker.sh k6_hotpath_plateau.js
TARGET_RPS=1000 DURATION=22s MAX_VUS=800 ./scripts/run_k6_docker.sh k6_hotpath_plateau.js
TARGET_RPS=1500 DURATION=22s MAX_VUS=800 ./scripts/run_k6_docker.sh k6_hotpath_plateau.js
```

Profile E:

```bash
K6_PROFILE=smoke  ./scripts/run_k6_docker.sh k6_ceiling.js   # CI-safe ≤15 RPS
K6_PROFILE=laptop ./scripts/run_k6_docker.sh k6_ceiling.js   # laptop overlay breakpoint
# Do not run K6_PROFILE=full on this laptop (8k / 1000–8000 VUs).
```

Ceiling contract test: `uv run pytest tests/unit/test_load_grafana_helpers.py::test_k6_ceiling_is_grafana_breakpoint -q`

## Idle (host NAT) — L1 image

| Call | HTTP | Time | Notes |
|------|------|------|--------|
| `GET /health/ready` | 200 | **18.2 ms** | |
| `POST /v1/entitlements/evaluate` | 200 | **31.78 ms** | `cache_hit: false` (after invalidate) |
| `POST /v1/entitlements/evaluate` | 200 | **2.34 ms** | `cache_hit: true` |

Idle is a single client on `localhost:8000`. k6 below talks to `billing-api:8000` on the compose network (no host NAT) after a warmup POST.

## k6 plateaus (`constant-arrival-rate`, 22 s) — L1

Executor: [constant-arrival-rate](https://grafana.com/docs/k6/latest/using-k6/scenarios/executors/constant-arrival-rate/). Script: [`k6_hotpath_plateau.js`](k6_hotpath_plateau.js). Warmup evaluate in `setup()`. `MAX_VUS=800`.

Hold = target ≈ achieved, 0% fail, 0 dropped. Break = first plateau that is not a hold.

| Target RPS | Achieved (`http_reqs`) | fail % | dropped | p50 (`med`) | p99 | `billing-api` CPU | Verdict |
|------------|------------------------|--------|---------|-------------|-----|-------------------|---------|
| 300 | 300.18 /s | 0.00% | 0 | 2.26 ms | 54.47 ms | 63–122% | hold |
| 400 | 400.06 /s | 0.00% | 0 | 2.21 ms | 326.81 ms | 20–101% | hold |
| 500 | 499.69 /s | 0.00% | 0 | 2.47 ms | 150.54 ms | 84–134% | hold |
| 700 | 700.24 /s | 0.00% | 0 | 2.74 ms | 150.4 ms | 84–244% | hold (last ~2.7 ms p50) |
| 1000 | 998.35 /s | 0.00% | 0 | 5.66 ms | 694.09 ms | 14–300% | **last hold** |
| 1500 | 1332.47 /s | 0.00% | 2671 | 203.94 ms | 3.23 s | 49–443% | **break** |

Confirm on a fresh overlay (in-run `docker stats`):

| Target RPS | Achieved | fail % | dropped | p50 | p99 | `billing-api` CPU | postgres CPU | redis CPU |
|------------|----------|--------|---------|-----|-----|-------------------|--------------|-----------|
| 1000 | 991.58 /s | 0.00% | 0 | 11.15 ms | 1.49 s | 15–298% | 2–40% | 0.2–4.3% |
| 1500 | 1362.71 /s | 0.00% | 2307 | 232.33 ms | 2.69 s | 160–435% | 11–24% | 0.4–4.2% |

Headline for spec footnote: **last hold 1000 RPS** evaluate (0% fail, 0 dropped, 22 s), cache-hit **p50 ≈ 6–11 ms**; **break 1500 RPS**; 1 replica, 4 workers, 2026-03-04. Last plateau with p50 still ≈ 2.7 ms is **700 RPS**.

`MAX_VUS=800` was enough that 300–1000 did not drop (1000 hunt: VUs peaked 300/800). At 1500, VUs reached 800/800 while p50 was already hundreds of ms — Grafana [dropped_iterations](https://grafana.com/docs/k6/latest/using-k6/scenarios/concepts/dropped-iterations/): later drops with rising latency mean **SUT degradation**, not VU starvation at ~2 ms. Did not raise VUs to “hold” 1500 while p50 stayed ~200 ms.

## Profile E — Grafana breakpoint (L1 image)

[`k6_ceiling.js`](k6_ceiling.js) uses Grafana [breakpoint](https://grafana.com/docs/k6/latest/testing-guides/test-types/breakpoint-testing/) shape: [`ramping-arrival-rate`](https://grafana.com/docs/k6/latest/using-k6/scenarios/executors/ramping-arrival-rate/) + [`abortOnFail`](https://grafana.com/docs/k6/latest/using-k6/thresholds/). Executor in this file is **only** `ramping-arrival-rate` (no `constant-arrival-rate`).

| Profile | Ramp | VUs | `http_req_failed` | `delayAbortEval` |
|---------|------|-----|-------------------|------------------|
| `smoke` | 5→15 RPS, hold 15 for 20 s | 8 / 20 | `rate<0.5` | 10 s |
| `laptop` | 100→2000 RPS over 3 min (one ramp, no plateau) | 400 / 800 | `rate<0.05` | 20 s |
| `full` | 0→`K6_CEILING_RPS` (default 8 000) over 3 min | 1000 / 8000 | `rate<0.05` | 30 s |

**Laptop** (one run; **not** `full`): `abortOnFail` on `http_req_failed` **did not fire** (fail **0.05%** = 101/174055; threshold `rate<0.05` is 5%). The scenario ran to ramp-end; k6 exit 0; end-of-test thresholds passed (`dropped_iterations` 14930 < 100000; p99 3.35 s < 5 s). **Last progress-line `iters/s` before completion:** **1999.92** (scheduled arrival at 3m0s). That is **not** achieved evaluate RPS. Whole-test `http_reqs` average **965.64 /s** must **not** be used as abort RPS. First time `maxVUs` exhausted: **1291.34 iters/s** at 1m52.9s. p50 **155.35 ms**, p99 **3.35 s**. `billing-api` CPU in the back half ≈ **397–425%** (4 workers pegged). Checks: 173953 status 200 / 101 not 200. API logs for this container: **86** evaluate HTTP 500 (`IndexError: pop from empty list` 81 parsed frames; `redis.exceptions.MaxConnectionsError` 9). `/health/ready` stayed HTTP 200 after this run.

**Full** profile E still ramps toward 8 000 — **stand only**; **not executed here.**

## Limiter

| Candidate | L1 evidence |
|-----------|-------------|
| HTTP 5xx on plateaus | **No.** fail 0.00% on 300–1500 plateaus and confirm. |
| HTTP 5xx on laptop breakpoint | **Present, small.** 86 logged evaluate 500s; k6 `http_req_failed` 0.05% — below `abortOnFail` (`rate<0.05` = 5%). |
| Timeouts / transport fail | **Not the plateau break.** Plateaus: `http_req_failed` 0.00%. Breakpoint max duration 15.01 s (some of the 101 k6 failures may be client-side). |
| k6 VU starvation at ~2 ms | **No.** 300–1000 RPS: 0 dropped at `MAX_VUS=800` with spare VUs (1000 hunt peak 300/800). |
| Latency blow-up | **Yes** as symptom: p50 ~2.7 ms at 700 → ~6–11 ms at 1000 → ~204–232 ms at 1500. |
| `dropped_iterations` | **Yes** as k6 symptom at 1500 (Grafana: later drops → SUT latency). Breakpoint dropped 14930 while mostly returning 200. |
| `billing-api` CPU | **Primary limiter.** Confirm 1500: API **160–435%** CPU (4 workers). Postgres ~11–24%, Redis ~0.4–4% — not the first wall. |

## Extrapolation (order-of-magnitude, not a measurement)

Last hold on this overlay: **1000 RPS / 4 workers = 250 RPS per worker** (p50 ≈ 6–11 ms, 0% fail, 0 dropped, 22 s). Last hold with p50 still ≈ 2.7 ms: **700 RPS / 4 ≈ 175 RPS/worker**.

If capacity scaled **linearly with those workers**, 3 replicas × 4 workers would be on the order of **~3k RPS** at the 1000 RPS hold (or **~2.1k RPS** at the 700 RPS / 2.7 ms p50 hold). Stand targets (§8.1 / §8.1.1) require a capable stand (CPU, pools, ≥3 API replicas, k6 runner).

## Pre-L1 baseline (same overlay knobs, SHA-256, no process L1)

Same 1 replica / 4 workers / pool 8+4 / rate limit 0 / OTEL off. Task 4 planned stop at 150 RPS; Task 5 ceiling hunt `MAX_VUS=400`.

Idle (host NAT): ready 10.34 ms; evaluate miss 34.06 ms; evaluate hit 12.39 ms.

| Target RPS | Achieved | fail % | dropped | p50 | p99 | Verdict |
|------------|----------|--------|---------|-----|-----|---------|
| 15 | 15.07 /s | 0.00% | 0 | 6.02 ms | 11.00 ms | hold |
| 40 | 40.17 /s | 0.00% | 0 | 6.23 ms | 10.28 ms | hold |
| 80 | 80.17 /s | 0.00% | 0 | 6.17 ms | 59.54 ms | hold |
| 150 | 150.38 /s | 0.00% | 0 | 6.08 ms | 21.45 ms | hold (planned stop) |
| 200 | 197.17 /s | 0.00% | 0 | 7.97 ms | 463.28 ms | hold |
| 300 | 300.45 /s | 0.00% | 0 | 8.37 ms | 405.86 ms | hold (last ~8 ms p50) |
| 400 | 400.05 /s | 0.00% | 0 | 34.03 ms | 650.36 ms | **last hold** |
| 500 | 485.95 /s | 0.00% | 212 | 252.27 ms | 2.25 s | **break** |

Confirm then: 400 hold (389.14 /s, p50 32.64 ms, API 258–371%); 500 break (477.02 /s, dropped 412, p50 311.91 ms, API 393–406%). Laptop breakpoint: `abortOnFail` did not fire (fail 0.00%); last progress-line **1999.92 iters/s** scheduled; whole-test `http_reqs` **466.11 /s** not abort RPS; dropped 104528; p99 8.94 s; `/health/ready` failed until recreate.

## Scope notes (this file)

- Current measurements are from the **laptop overlay** in the table above (1 replica, 4 workers, auth L1 + snapshot L1, 2026-03-04).
- Profile E laptop: `http_req_failed` 0.05%; **1999.92 iters/s** at ramp-end is **scheduled arrival**, not achieved evaluate RPS; whole-test `http_reqs` average (**965.64 /s**) is not abort RPS.
- §8.1.1 profile A acceptance requires a capable stand (≥3 API replicas, 10 min hold) — see [`profile-a-report.md`](profile-a-report.md).
