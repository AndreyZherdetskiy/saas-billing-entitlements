# Billing & Entitlements Platform

Organization → subscription → entitlements → mock Stripe webhook → transactional outbox → Kafka → ledger → reconciliation. Local clone-to-demo in about 15 minutes (no remote deploy). Product contract: `spec.md`. Agent rules: `AGENTS.md`.

**Stack:** Python 3.12 · FastAPI · async SQLAlchemy · PostgreSQL · Redis · Kafka · Celery (worker/beat) · `outbox-relay` · mock Stripe · Helm · k6 (load DoD) · Locust (additive smoke).

## Architecture

Local compose (production path is Helm/k8s):

```mermaid
flowchart LR
    subgraph clients [Clients / demo]
        demo_ui["demo-ui 8080"]
        mock["mock-stripe 8001"]
        k6["k6 / curl"]
    end

    subgraph app [Application]
        api["billing-api 8000"]
        worker["billing-worker"]
        beat["billing-beat"]
        relay["outbox-relay"]
    end

    subgraph data [Data and bus]
        pg["PostgreSQL"]
        redis["Redis"]
        kafka["Kafka"]
    end

    kafbat["kafbat-ui 8081"]

    demo_ui --> api
    k6 --> api
    mock -->|webhooks| api
    api --> pg
    api --> redis
    worker --> pg
    beat --> pg
    api -->|domain facts to outbox| pg
    relay -->|poll and publish| pg
    relay --> kafka
    kafbat -.->|read-only UI| kafka
```

`POST /v1/entitlements/evaluate` reads Redis/PG only — no outbox, no Kafka. Usage ingest writes PG only. Kafka gets domain facts **after commit**, via `outbox-relay` (webhooks, subscription lifecycle, ledger, recon, dunning, period close).

**Not in this repo:** live Stripe (port + mock only), customer portal, DB sharding, HTTP idempotency response cache. Prometheus/Grafana is an opt-in compose profile, not the default.

## Quick start

Need Docker Compose v2, [uv](https://docs.astral.sh/uv/), Python 3.12. Host ports: `5432` PG, `6379` Redis, `9092` Kafka, `8000` API, `8001` mock-stripe, `8080` demo-ui, `8081` Kafbat. Compose project is always **`billing-platform`** (`make` passes `-p`). After `git add`, do not force-add gitignored secrets (`.env`, `.local/`).

```bash
uv sync
cp .env.example .env          # single SoT; do not create deploy/compose/.env
make compose-core             # alias: make compose-up
curl -s http://localhost:8000/health/live
curl -s http://localhost:8000/health/ready | jq .
```

`billing-api` on start runs `alembic upgrade head` and a deterministic demo seed (`RUN_MIGRATIONS` / `RUN_DEMO_SEED`, default true). Demo key/org in `.env.example` match the seed — demo-ui and k6 work after `cp` with no extra seed.

| Make target | What starts |
|-------------|-------------|
| `compose-core` | PG primary, Redis, Kafka, API, worker, beat, relay, mock-stripe, kafbat-ui, demo-ui |
| `observability-up` | core + LGTP (Grafana `:3000`, OTLP `:4318`) |
| `compose-all` | everything: replica, PgBouncer, PgBouncer-RO, LGTP |
| `perf-up` | core + overlay: 4 API workers, pool 2+1, rate limits 0, OTel off, relay×2 — characterization only; tear down with `compose-down` |
| `compose-down` | all profiles |

Pick **one** up-target; profiles are opt-in. Image prune is project-labeled only — never `system`/`container` prune.

If Kafka is slow locally: `HEALTH_KAFKA_OPTIONAL=true` → ready `degraded` (200) instead of 503. Compose API healthcheck is `/health/ready`; worker is `celery inspect ping`; **billing-beat** has no probe (scheduler). Helm splits API live (`/health/live`) vs ready.

JSON bodies need `Content-Type: application/json` (FastAPI `strict_content_type` → **422** without it).

**WSL2:** if `:8000` RSTs from the Linux distro while containers are healthy, use Windows `localhost` or `docker exec` into `billing-api`.

### Env, secrets, rate limits

Local/demo only: Postgres `billing:billing`, webhook secret `whsec_mock_dev`. Rotate locally with `MOCK_STRIPE_WEBHOOK_SECRET_PREVIOUS` overlapping the old value. Host tools must use `127.0.0.1:5432`; containers keep hostname `postgres`.

| Purpose | Variables |
|---------|-----------|
| Admin / k6 / demo-ui | `K6_API_KEY` = `DEMO_UI_API_KEY` = `bp_local_demo_platform_admin_key_v1` |
| Demo org | `K6_ORG_ID` = `DEMO_UI_ORG_ID` = `01900000-0000-7000-8000-000000000001` |
| Org keys | `API_RATE_LIMIT_PER_MINUTE=120` |
| `platform_admin` | `API_RATE_LIMIT_PLATFORM_ADMIN_PER_MINUTE=1000` |

Exempt from API-key limits: `/health/live`, `/health/ready`. Webhooks are HMAC (`Stripe-Signature`) only — not API-key limited. `limit=0` disables Redis limiting (**perf/test stands only, never production**). Redis down → HTTP **503** (`rate limiting temporarily unavailable`), not open flood. `make load-*` recreates `billing-api` with both limits `0` and `OTEL_SDK_DISABLED=true`.

Host migrate / re-seed (optional; compose already did this):

```bash
DATABASE_URL=postgresql+asyncpg://billing:billing@127.0.0.1:5432/billing \
  uv run alembic upgrade head
DATABASE_URL=postgresql+asyncpg://billing:billing@127.0.0.1:5432/billing \
  uv run python -m billing_platform.bootstrap
# rollback smoke: uv run alembic downgrade -1
```

Demo seed is **one** trial tenant. Multi-org / usage / Kafka evidence:

```bash
DATABASE_URL=postgresql+asyncpg://billing:billing@127.0.0.1:5432/billing \
  uv run python scripts/seed_prod_like.py --profile medium
```

Idempotent. Without `--purge-prod-like-prefix`, API keys accumulate (`pl_` rows only; Kafka history is not deleted).

Open **http://localhost:8080** (demo-ui) or **http://localhost:8000/docs**. Leave `DEMO_UI_API_BASE_URL` **empty** in Docker so nginx proxies `/v1` and `/health` same-origin. Setting `http://localhost:8000` makes the browser go cross-origin and show **Failed to fetch**.

## Demo walkthrough

Seed: trial 14 days, `trialing`, `external_subscription_id=sub_demo_seed_001`.

```bash
API_KEY=bp_local_demo_platform_admin_key_v1
ORG=01900000-0000-7000-8000-000000000001
EXT_SUB=sub_demo_seed_001
```

1. **Evaluate twice** (demo-ui Entitlements, or curl). Second call: `"cache_hit": true`.
2. **Activate** via mock Stripe `invoice.paid` → subscription `active`, ledger + Kafka (relay must be up).
3. **Usage** → recon mismatch → optional `payment_failed` / dunning → Kafbat.

```bash
curl -s -X POST http://localhost:8000/v1/entitlements/evaluate \
  -H "Authorization: Bearer $API_KEY" -H 'Content-Type: application/json' \
  -d "{\"organization_public_id\": \"${ORG}\", \"checks\": [{\"feature_key\": \"api_calls\"}]}" \
  | jq '{cache_hit, subscription_status, results}'

curl -s -X POST http://localhost:8001/v1/test/emit-webhook \
  -H 'Content-Type: application/json' \
  -d "{\"event_type\": \"invoice.paid\", \"data\": {\"id\": \"in_demo_paid_001\", \"object\": \"invoice\", \"subscription\": \"${EXT_SUB}\", \"status\": \"paid\", \"amount_paid\": 2900, \"currency\": \"usd\"}}"

curl -s -H "Authorization: Bearer $API_KEY" \
  "http://localhost:8000/v1/organizations/${ORG}/subscriptions" | jq '.[0].status'
```

Cache invalidation after webhook is automatic; a later evaluate shows `subscription_status: active`.

`POST /v1/test/emit-webhook` mints a **new** `provider_event_id` every call. Repeating the same invoice JSON is not a provider redelivery: ledger/outbox stay put; an extra `webhook_events` row may be `failed` (`active → active` is illegal). True duplicates key on `provider_event_id` (`ON CONFLICT DO NOTHING`). Replay a persisted failed row: `POST /v1/admin/webhooks/{id}/replay`.

Usage (product_service or platform_admin; batch ≤1000). Aggregates are hourly; the hour that contains `current_period_start` counts even if the period started mid-hour. Celery sweep `usage.aggregate_hourly_sweep` (beat at minute 5) rolls events into `usage_aggregates_hourly` — evaluate `used` reads those aggregates.

```bash
curl -s -X POST http://localhost:8000/v1/usage/events/batch \
  -H "Authorization: Bearer $API_KEY" -H 'Content-Type: application/json' \
  -d "{\"organization_public_id\": \"${ORG}\", \"events\": [{\"feature_key\": \"api_calls\", \"quantity\": 1, \"idempotency_key\": \"demo-usage-001\"}]}"
```

Recon mismatch:

```bash
DATABASE_URL=postgresql+asyncpg://billing:billing@127.0.0.1:5432/billing \
  uv run python scripts/seed_recon_mismatch.py
curl -s -X POST http://localhost:8000/v1/admin/reconciliation/run \
  -H "Authorization: Bearer $API_KEY" -H 'Idempotency-Key: demo-recon-run-001' | jq .
# then GET /v1/admin/reconciliation/runs/{run_id}/discrepancies
```

Dunning: set `DUNNING_ENABLED=true` on API and `billing-worker`, restart, emit `invoice.payment_failed` for `$EXT_SUB` → `past_due`. List campaigns: `GET /v1/admin/dunning/campaigns`. Pause/resume is Admin API only (`POST …/campaigns/{id}/pause|resume`); demo-ui is display-only.

Outbox lag check: `docker compose -p billing-platform -f deploy/compose/docker-compose.yml stop outbox-relay` → `/health/ready` → `start outbox-relay`.

## Demo UI

Thin Vite + React + TypeScript in `demo_ui/`. Logic stays on the server.

| Screen | API |
|--------|-----|
| Organization | `GET /v1/organizations/{organization_public_id}` |
| Subscription | `GET /v1/organizations/{organization_public_id}/subscriptions` |
| Entitlements | `POST /v1/entitlements/evaluate` |
| Usage | `GET /v1/organizations/{organization_public_id}/usage` |
| Reconciliation | `GET /v1/admin/reconciliation/runs`, `…/runs/{id}/discrepancies` |
| Dunning | `GET /v1/admin/dunning/campaigns` |
| Webhook status | `/health/live`, `/health/ready` + poll subscription |

Docker injects `DEMO_UI_API_KEY` / `DEMO_UI_ORG_ID` / `DEMO_UI_FEATURE_KEYS` into `runtime-config.js` at start (not baked into the image). Local Vite: `VITE_API_BASE_URL=http://localhost:8000` only with CORS or a Vite proxy. Do **not** `npm run build` with real `VITE_*` keys — they embed in `dist/`. `GET /v1/admin/webhooks` is not implemented; the webhook screen is health + subscription poll.

## Kafka

Topics (not `*.v1`): `billing.subscription.events`, `billing.invoice.events`, `billing.ledger.events`, `billing.reconciliation.events`, `billing.entitlement.events`, `billing.dlq`. UI: **http://localhost:8081** (cluster `local`). Relay **publishes**; consumer groups appear when something consumes.

| Action | Kafka |
|--------|--------|
| Evaluate (even cache hit) | none |
| Usage batch alone | none (PG only) |
| Webhook → subscription, ledger, recon, dunning, period close | `outbox` → relay → `billing.*` |

Look at **recent** messages after webhook / `seed_prod_like`, not earliest offset (old payloads may still show BIGINT internals). Host `aiokafka` on `localhost:9092` is not supported (advertised listeners). In-container:

```bash
docker compose -p billing-platform -f deploy/compose/docker-compose.yml exec kafka \
  /opt/kafka/bin/kafka-console-consumer.sh \
  --bootstrap-server localhost:9092 --topic billing.subscription.events --from-beginning
```

`make test-integration` is host pytest + Testcontainers — it does **not** start this compose stack and excludes `live_compose` probes. After `make compose-core`, live probes are `uv run pytest -m live_compose` (CI `load-locust-smoke`). Kafbat health: `curl -s http://localhost:8081/actuator/health`.

## Health, logs, traces

| Endpoint | Meaning |
|----------|---------|
| `GET /health/live` | liveness, no dependency checks |
| `GET /health/ready` | ping PostgreSQL, Redis, Kafka |

SIGTERM: Uvicorn drains in-flight work up to `SHUTDOWN_GRACE_SECONDS` (default 30), then closes Redis and the PG pool. `docker compose -p billing-platform -f deploy/compose/docker-compose.yml stop billing-api`.

Logs are structlog JSON: `correlation_id` (`X-Correlation-ID` or a new UUID, echoed on the response), `organization_id` when a tenant key is present, `request_completed` with `duration_ms`.

Default compose: `OTEL_SDK_DISABLED=true` (safe for clone + k6; SLO metric stubs no-op). Tracing: `OTEL_SDK_DISABLED=false` and `OTEL_EXPORTER_OTLP_ENDPOINT` as the collector **base** URL (`http://localhost:4318` or `http://alloy:4318`); the app appends `/v1/traces` and `/v1/metrics`. Empty OTLP + SDK on → Console exporter (avoid under k6). Empty string for `OTEL_SDK_DISABLED` is treated as **false** — set `true` or `false` explicitly.

`make observability-up` or `compose-all` sets OTLP to Alloy. Grafana **http://localhost:3000** (`admin`/`admin`, local only). Tail-based sampling keeps errors, ≥100ms, billing spans (`webhook.process`, `outbox.relay.batch`, `reconciliation.run`, `dunning.attempt`), plus 5% baseline — quiet Tempo after evaluate-only success is expected. Retention: traces 48h, logs/metrics 72h. Mimir / prod object storage deferred.

## Quality and load

```bash
make lint typecheck
make test-unit              # coverage ≥ 80% services+domain; Docker / PostgresContainer
make test-integration       # Testcontainers; needs helm for chart tests
make test                   # lint + typecheck + unit + integration

uv sync --group load
make load-smoke-all         # k6 A→E smoke (host k6 or Docker fallback)
make load-smoke-all-soft    # same, --no-thresholds
make load-a-full            # stand ≥3 API replicas; also load-b-full … load-e-full
make load-locust            # 5 users / 10s; not a substitute for k6 A/C DoD
```

Needs `K6_API_KEY` + `K6_ORG_ID`. Scripts under `docs/perf/`. Smoke stays ≤15 RPS (rate-limit-safe even before Makefile sets limits to 0). Laptop = smoke / characterization. Full A/C belongs on a stand with ≥3 API replicas.

| Profile | Script | Smoke | Full (DoD) |
|---------|--------|-------|------------|
| A evaluate | `k6_evaluate_peak.js` | 15 RPS / 30s | **3,000 RPS / 10 min** (required) |
| B usage | `k6_usage_ingest.js` | ~100 events/s / 30s | 1,500 events/s / 10 min |
| C mixed | `k6_mixed.js` | 9+4+2 RPS / 30s | **5,000 HTTP RPS / 10 min** (required) |
| D soak | `k6_soak.js` | 2 min at 0.3× C mix | 30–60 min |
| E ceiling | `k6_ceiling.js` | ~30 s to 15 RPS | ramp until abort (optional) |

Laptop evaluate overlay (`make load-*`, 1 replica / 4 workers, pool 8+4): last hold **1000 RPS** / break **1500 RPS**. `make perf-up` hunt (pool 2+1, relay×2): last hold **1500 RPS** / break **2000 RPS**. Locust UI: `make load-locust-ui` on `:8089` (fails if busy). Locust+Grafana: `make load-locust-otel`. k6 Prometheus RW: `make load-k6-grafana` (needs observability).

## Stage 3 ops (local)

```bash
helm template billing-platform deploy/helm/billing-platform   # api, worker, beat, relay + HPA
```

Read replica: compose profile `postgres-replica`; set `DATABASE_READ_URL`; evaluate cache-miss may use RO when replica lag is under `REPLICA_LAG_THRESHOLD_SECONDS` (default 30). Relay HA: two relay replicas; integration `test_relay_two_replicas_no_dup.py`. DLQ: `scripts/replay_outbox_dlq.py`. API key / webhook rotation: admin rotate + overlapping secrets.

**Stuck outbox:** unpublished rows while relay is down or erroring — restart relay; lag should drain. **Webhook `failed` / still trialing:** check HMAC secret, then `POST /v1/admin/webhooks/{id}/replay`. **Recon discrepancy:** inspect provider vs ledger on the run, then ops follow-up. **Dunning stuck:** pause/resume the campaign; worker must have `DUNNING_ENABLED=true`.

Accepted ADRs live in `docs/adr/` (no sharding in stages 1–3; LGTP scoped adopt; idempotency responses deferred). Stage plans: `docs/plans/`. Load A/C full RPS on a laptop remains smoke-only.
