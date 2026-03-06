# Profile A — Evaluate peak load report

**Spec:** §8.1.1 profile A, §10.5, §11.3
**Task:** 48
**Date:** 2026-03-04
**Script:** [`k6_evaluate_peak.js`](k6_evaluate_peak.js)

## Target (§8.1.1)

| Parameter | Value |
|-----------|--------|
| Endpoint | `POST /v1/entitlements/evaluate` |
| Load | **3 000** RPS (cached-heavy evaluate hot path) |
| Duration | **10 min** sustained |
| Error rate | < **0.1%** |
| Latency | p99 < **50 ms** |
| Topology | ≥ **3** API replicas |

## Overall verdict: **PARTIAL**

Full profile A criteria were **not** met on the local laptop smoke run documented below. Acceptance requires a capable stand (≥3 API replicas, adequate CPU/RAM, k6 runner co-located or low-latency to the cluster). No human waiver.

| Criterion | Full stand required | Smoke run (this report) | Status |
|-----------|---------------------|-------------------------|--------|
| 3 000 RPS / 10 min | Yes | 50 RPS target / 30 s | **PARTIAL** — smoke only |
| p99 < 50 ms | Yes | p99 ≈ **33.68 s** | **FAIL** (overload) |
| error rate < 0.1% | Yes | **8.48%** | **FAIL** |
| ≥3 API replicas | Yes | **1** (`docker compose`) | **BLOCKER** |

## Stand configuration (smoke run)

| Component | Value |
|-----------|--------|
| Host | WSL2 Linux (developer laptop) |
| API replicas | **1** (`billing-api` in `deploy/compose/docker-compose.yml`) |
| Postgres | 1 primary (default compose; optional `--profile postgres-replica` for RO) |
| Redis | 1 instance |
| k6 | `grafana/k6` Docker image v2.1.0 |
| k6 profile | `K6_PROFILE=smoke` (50 iter/s, 30 s) — not full profile A |

**Full profile A stand (not executed here):** Helm/kind or production-like cluster with `replicaCount.api ≥ 3`, external Postgres/Redis, k6 on dedicated runner; use `K6_PROFILE=full` in the script.

## Prerequisites

```bash
# From repo root
uv sync
docker compose -f deploy/compose/docker-compose.yml up -d --build
DATABASE_URL=postgresql+asyncpg://billing:billing@localhost:5432/billing uv run alembic upgrade head
DATABASE_URL=postgresql+asyncpg://billing:billing@localhost:5432/billing uv run python scripts/seed_catalog.py

export K6_API_KEY=bp_local_demo_platform_admin_key_v1
export K6_ORG_ID=01900000-0000-7000-8000-000000000001
```

## Commands executed (smoke)

```bash
docker run --rm -i --add-host=host.docker.internal:host-gateway \
  -e K6_API_KEY -e K6_ORG_ID \
  -e BASE_URL=http://host.docker.internal:8000 \
  -e K6_PROFILE=smoke \
  -v "$PWD/docs/perf/k6_evaluate_peak.js:/scripts/k6_evaluate_peak.js" \
  grafana/k6 run /scripts/k6_evaluate_peak.js
```

**Full profile A (capable stand only):**

```bash
export BASE_URL=https://billing-api.stand.example   # ingress to ≥3 replicas
export K6_PROFILE=full
k6 run docs/perf/k6_evaluate_peak.js
# or same docker invocation with K6_PROFILE=full and BASE_URL pointed at the stand
```

## Smoke methodology note (post-P1, 2026-03-05)

The run below (2026-03-04) predates P1 fixes. **Rate-limit 429 confound:** Makefile `load-*` now sets `API_RATE_LIMIT_*=0` and recreates `billing-api` before k6 — re-smoke should not hit 429 from default 120/1000 req/min limits. **OTel:** disable Console exporter (`OTEL_SDK_DISABLED=true`) under load to avoid sync span I/O skewing latency.

**maxVUs / dropped iterations:** smoke targets 50 iter/s with `maxVUs=100`; laptop saturation → `dropped_iterations` and low achieved RPS are **expected** on single-replica compose. Use `make load-smoke-all` (not bare `k6 run`); if still saturated, `make load-smoke-all-soft` or accept PARTIAL script-validation evidence per §10.5.

## Smoke results (2026-03-04)

k6 thresholds (profile A SLO) were applied to smoke; expected to fail on laptop.

| Metric | Value |
|--------|--------|
| Target rate | 50 iter/s |
| Achieved `http_reqs` | **5.11/s** (224 requests in ~44 s incl. graceful stop) |
| `dropped_iterations` | **1281** (VU ceiling 100; insufficient for sustained rate) |
| `http_req_failed` | **8.48%** (19/224) |
| `http_req_duration` p99 | **33.68 s** |
| `http_req_duration` avg | 15.31 s |
| `checks` (status 200) | 91.32% |
| Max VUs | 100 |

**Idle baseline (3 sequential curls, no load):** ~**0.22 s** per evaluate (cache warm) — still above 50 ms SLO on single replica compose; indicates laptop/compose is not acceptance topology.

## Interpretation

1. **Topology:** Profile A acceptance explicitly requires ≥3 API replicas (§8.1.1). Compose smoke uses 1 replica — valid for script validation only (§10.5).
2. **VU saturation:** At 50 RPS target, k6 hit `maxVUs=100` and dropped 1281 iterations; effective throughput ~5 RPS. Full 3k RPS needs a dedicated load generator and horizontally scaled API tier.
3. **Next step for PASS:** Deploy chart with `replicaCount.api: 3` (or HPA min 3), seed tenant, run `K6_PROFILE=full` from a capable host; record stand CPU/RAM and pool sizes in this report.

## Sources consulted (k6 grounding)

- [Constant arrival rate executor](https://grafana.com/docs/k6/latest/using-k6/scenarios/executors/constant-arrival-rate/) — open-model RPS targeting
- [Open vs closed models](https://grafana.com/docs/k6/latest/using-k6/scenarios/concepts/open-vs-closed/) — why arrival-rate fits RPS SLA
- [Arrival-rate VU allocation](https://grafana.com/docs/k6/latest/using-k6/scenarios/concepts/arrival-rate-vu-allocation/) — `preAllocatedVUs` / `maxVUs` sizing

## Artifacts

| File | Purpose |
|------|---------|
| [`k6_evaluate_peak.js`](k6_evaluate_peak.js) | k6 scenario (`smoke` / `full` profiles) |
| This report | §8.1.1 profile A evidence |
