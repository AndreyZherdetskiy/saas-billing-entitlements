# Locust smoke report

**Spec:** §8.1.1 tooling (Locust additive); not profile A 3k RPS DoD
**Date:** 2026-03-05
**Harness:** `make load-locust` → `scripts/load_locust_smoke.sh` + `loadtests/locustfile.py`
**Locust:** 2.46.3 (`uv` group `load`)

## Verdict: **PASS** (smoke only)

Headless Locust against `make compose-core` completed with exit 0, ≥1 HTTP request, zero failures, and three named endpoints in stats. This is **not** profile A 3k RPS evaluate DoD — k6 remains the DoD tool.

## Stand

| Component | Value |
|-----------|--------|
| Host | WSL2 Linux laptop (`6.6.87.2-microsoft-standard-WSL2`), 16 logical CPUs, ~15 GiB RAM |
| API replicas | **1** (`billing-api` in `deploy/compose/docker-compose.yml`) |
| Stack | `make compose-core` (PG, Redis, Kafka, API, worker, beat, relay, mock-stripe, kafbat-ui, demo-ui) |
| Rate limits | Disabled for run via Makefile `_load_perf_rate_limits` (`API_RATE_LIMIT_*=0`) |
| OTel on API | `OTEL_SDK_DISABLED=true` (default load path) |

## Command

```bash
uv sync --group load
make compose-core
make load-locust
# defaults after Task 3: LOAD_USERS=5, LOAD_RUN_TIME=10s, host=BASE_URL
```

Artifacts: `.local/locust/smoke.html`, `.local/locust/smoke_*.csv` (gitignored).

## Results (primary evidence run)

Final verification after raising default `LOAD_USERS` to **5** (`make load-locust`, no overrides):

| Metric | Value |
|--------|--------|
| Users / spawn / duration | 5 / 1 per s / 10s |
| User mix spawned | EvaluateUser 3, UsageIngestUser 1, AdminReadUser 1 |
| Total requests | **39** |
| Failures | **0** (0.00%) |
| Aggregated req/s | ~4.15 |
| Aggregated p50 / p95 (ms) | **790** / **1100** |

| Name | # reqs | Fails | p50 (ms) | p95 (ms) |
|------|--------|-------|----------|----------|
| `POST /v1/entitlements/evaluate` | 24 | 0 | 790 | 1100 |
| `POST /v1/usage/events/batch` | 8 | 0 | 840 | 930 |
| `GET /v1/organizations/{org}/usage` | 7 | 0 | 850 | 970 |

Latency is laptop / single-replica smoke — not an SLO claim.

### Note on default users

An earlier run with `LOAD_USERS=2` exited 0 with 30 requests and **0%** fails, but Locust spawned only Evaluate + Usage (AdminRead weight too low for 2 users). Default smoke users were raised to **5** so all three endpoint names appear under `make load-locust`.

## Fail-closed evidence

Both paths use `./scripts/load_locust_smoke.sh` (same preflight as `make load-locust`, which sources `.env` and sets rate-limit overrides). Locust must not start on either failure.

### 1) Missing credentials (no Make / `.env` inheritance)

```bash
env -u K6_API_KEY -u K6_ORG_ID -u LOAD_API_KEY -u LOAD_ORG_ID -u LOAD_HOST \
  ./scripts/load_locust_smoke.sh
```

```text
[load-locust] preflight (credentials + /health/ready) host=http://localhost:8000 ...
preflight failed: missing load credentials: LOAD_API_KEY or K6_API_KEY, LOAD_ORG_ID or K6_ORG_ID
```

Exit **1** at credential preflight; Locust did not start.

### 2) Unreachable host (credentials from `.env`, bad host)

```bash
set -a && . ./.env && set +a
LOAD_HOST=http://127.0.0.1:1 ./scripts/load_locust_smoke.sh
```

Equivalent via Make (loads `.env` + Makefile overrides):

```bash
LOAD_HOST=http://127.0.0.1:1 make load-locust
```

```text
[load-locust] preflight (credentials + /health/ready) host=http://127.0.0.1:1 ...
preflight failed: API ready check failed for http://127.0.0.1:1/health/ready: [Errno 111] Connection refused
```

Exit **1** at `/health/ready` preflight; Locust did not start. No Compose stack required for these checks.

## Limits

- Not a substitute for k6 profiles A–E or full §8.1.1 intensity.
- Not multi-replica / stand evidence.
- Compose torn down after this task (`docker compose -p billing-platform … down`).

## Sources consulted

- https://docs.locust.io/en/2.46.3/running-without-web-ui.html
- https://docs.locust.io/en/2.46.3/configuration.html
- https://docs.locust.io/en/2.46.3/quickstart.html
- https://docs.locust.io/en/2.46.3/writing-a-locustfile.html
