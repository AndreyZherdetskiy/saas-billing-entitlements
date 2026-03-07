# Profile C — Mixed prod-like load report

**Spec:** §8.1.1 profile C, §10.5, §11.3
**Task:** 49
**Date:** 2026-03-04
**Script:** [`k6_mixed.js`](k6_mixed.js)

## Target (§8.1.1)

| Parameter | Value |
|-----------|--------|
| Mix | evaluate + usage ingest + light admin read |
| Total load | **5 000** HTTP RPS mix (evaluate **3 000** + usage **1 500** + admin **500**; band **4 500–6 000**) |
| Duration | **10 min** sustained |
| Profile A slice | error rate < **0.1%**; evaluate p99 < **50 ms** |
| Profile B slice | usage ingest 2xx / idempotency; no 5xx storm |
| Outbox | `outbox_lag_seconds` p99 < **30 s** under peak (metrics, not k6) |
| Topology | ≥ **3** API replicas (same stand as profile A) |

### Full-profile mix (script `K6_PROFILE=full`)

| Scenario | Endpoint | Rate |
|----------|----------|------|
| `mixed_evaluate` | `POST /v1/entitlements/evaluate` | 3 000 RPS |
| `mixed_usage` | `POST /v1/usage/events/batch` (1 event) | 1 500 RPS |
| `mixed_admin_read` | `GET /v1/organizations/{org}/usage` | 500 RPS |
| **Total** | | **5 000** RPS |

## Overall verdict: **PARTIAL**

Full profile C criteria were **not** met on the local laptop smoke run documented below. Acceptance requires a capable stand (≥3 API replicas, worker + relay, adequate CPU/RAM, k6 runner co-located or low-latency to the cluster). No human waiver.

| Criterion | Full stand required | Smoke run (this report) | Status |
|-----------|---------------------|-------------------------|--------|
| 5 000 RPS / 10 min | Yes | 50 RPS target / 30 s | **PARTIAL** — smoke only |
| evaluate p99 < 50 ms | Yes | p99 ≈ **36.16 s** | **FAIL** (overload) |
| evaluate error < 0.1% | Yes | **59.52%** | **FAIL** |
| usage 2xx / no storm | Yes | **33.33%** failed | **FAIL** |
| `outbox_lag_seconds` p99 < 30 s | Yes (metrics) | **not measured** | **N/A** smoke |
| ≥3 API replicas | Yes | **1** (`docker compose`) | **BLOCKER** |

## Stand configuration (smoke run)

| Component | Value |
|-----------|--------|
| Host | WSL2 Linux (developer laptop) |
| API replicas | **1** (`billing-api` in `deploy/compose/docker-compose.yml`) |
| Postgres | 1 primary (default compose; optional `--profile postgres-replica` for RO) |
| Redis | 1 instance |
| k6 | `grafana/k6` Docker image v2.1.0 |
| k6 profile | `K6_PROFILE=smoke` (50 iter/s total, 30 s) — not full profile C |

**Full profile C stand (not executed here):** Helm/kind or production-like cluster with `replicaCount.api ≥ 3`, worker + outbox-relay, external Postgres/Redis/Kafka; use `K6_PROFILE=full`; capture `outbox_lag_seconds` from OTel/Prometheus during the run.

## Prerequisites

```bash
# From repo root — rebuild API so usage routes are present
docker compose -f deploy/compose/docker-compose.yml up -d --build billing-api
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
  -v "$PWD/docs/perf/k6_mixed.js:/scripts/k6_mixed.js" \
  grafana/k6 run /scripts/k6_mixed.js
```

**Full profile C (capable stand only):**

```bash
export BASE_URL=https://billing-api.stand.example   # ingress to ≥3 replicas
export K6_PROFILE=full
k6 run docs/perf/k6_mixed.js
# During run: sample outbox_lag_seconds p99 from metrics backend (see docs/slo.md)
```

## Smoke methodology note (post-P1, 2026-03-05)

The run below (2026-03-04) predates P1 fixes. **Rate-limit 429 confound:** Makefile `load-*` now disables API rate limits before k6 — mixed evaluate/usage/admin should not fail on 429 from default limits when using `make load-c` / `make load-smoke-all`. **OTel:** set `OTEL_SDK_DISABLED=true` for load runs.

**maxVUs:** smoke uses combined 50 iter/s across three scenarios (`maxVUs` 80/50/30); dropped iterations on laptop ≠ SLO failure — see `docs/perf/README.md` § «Smoke maxVUs».

## Smoke results (2026-03-04)

k6 thresholds (profile A/B slices + dropped iterations) were applied to smoke; expected to fail on laptop.

| Metric | Value |
|--------|--------|
| Target rates | evaluate 30 + usage 15 + admin 5 = **50 iter/s** |
| Achieved `http_reqs` | **2.13/s** (84 requests in ~39.5 s incl. graceful stop) |
| `dropped_iterations` | **1316** (VU ceilings 80/50/30 hit) |
| `http_req_failed` (all) | **50.00%** (42/84) |
| `http_req_failed{path:evaluate}` | **59.52%** |
| `http_req_failed{path:usage}` | **33.33%** |
| `http_req_failed{path:admin_read}` | **66.66%** |
| `http_req_duration{path:evaluate}` p99 | **36.16 s** |
| `http_req_duration` avg | 22.96 s |
| `checks` succeeded | 45.45% (35/77) |
| Max VUs | 160 |

**Note:** Initial smoke against a stale API image (without `/v1/usage/*` routes) failed at setup with HTTP 404; `billing-api` was rebuilt before the run documented above.

**Outbox lag:** Not sampled on smoke (single replica, no sustained peak). On full run, record `outbox_lag_seconds` p99 from metrics per §8.1.1 profile C.

## Interpretation

1. **Topology:** Profile C acceptance assumes the same multi-replica stand as profile A (§8.1.1). Compose smoke uses 1 replica — valid for script validation only (§10.5).
2. **VU saturation:** At 50 RPS combined target, k6 saturated `maxVUs` on all three scenarios and dropped 1316 iterations; effective throughput ~2 RPS. Full 5k RPS needs a dedicated load generator and horizontally scaled API tier.
3. **Mixed contention:** Concurrent evaluate + usage + read amplifies PG/Redis pressure vs profile A alone; outbox lag must be verified under full mixed peak, not infer from evaluate-only runs.
4. **Next step for PASS:** Deploy chart with `replicaCount.api: 3`, ensure worker + relay running, seed tenant, run `K6_PROFILE=full`, capture outbox metrics; update this report.

## Sources consulted (k6 grounding)

- [Constant arrival rate executor](https://grafana.com/docs/k6/latest/using-k6/scenarios/executors/constant-arrival-rate/) — open-model RPS targeting per scenario
- [Multiple scenarios](https://grafana.com/docs/k6/latest/using-k6/scenarios/) — parallel mixed workloads
- [Thresholds with tags](https://grafana.com/docs/k6/latest/using-k6/thresholds/) — per-path SLO checks

## Artifacts

| File | Purpose |
|------|---------|
| [`k6_mixed.js`](k6_mixed.js) | k6 mixed scenario (`smoke` / `full` profiles) |
| [`profile-a-report.md`](profile-a-report.md) | Profile A evidence (Task 48) |
| This report | §8.1.1 profile C evidence |
