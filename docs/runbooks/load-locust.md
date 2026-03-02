# Runbook: Locust load smoke

**Context:** host-side Locust smoke alongside k6 profiles A–E. Locust is **additive** — it does **not** replace k6 and is **not** profile A **3k RPS** DoD.
**Entry:** `make load-locust` / `make load-locust-ui` · package `loadtests/` · script `scripts/load_locust_smoke.sh`.

## Symptoms / when to use

- Validate evaluate / usage ingest / admin usage-read HTTP paths after compose changes.
- Prefer Locust when you want a Python `uv` workflow or the Web UI; use k6 for §8.1.1 DoD intensity.

## Prerequisites

1. Core stack up and ready:
   ```bash
   make compose-core
   curl -sf http://localhost:8000/health/ready
   ```
2. Repo-root `.env` with demo credentials (from `.env.example`):
   - `K6_API_KEY` / `K6_ORG_ID` (or `LOAD_API_KEY` / `LOAD_ORG_ID`)
   - `BASE_URL` (default `http://localhost:8000`)
3. Locust install:
   ```bash
   uv sync --group load
   ```

`make load-locust` recreates `billing-api` with rate limits disabled and OTel off (`_load_perf_rate_limits`) — local perf only. For a prod-like ceiling stack (4 Uvicorn workers, pool 2+1, relay×2), use `make perf-up` instead of `compose-core` — see [local-compose-profiles.md](local-compose-profiles.md).

## Headless smoke

```bash
make load-locust
```

Defaults: **5 users**, spawn rate **1**/s, run time **10s**, host from `LOAD_HOST`/`BASE_URL`. Artifacts under `.local/locust/` (`smoke.html`, `smoke_*.csv`) — gitignored.

Official flags (Locust 2.46.3): `--headless`, `-u`/`-r`/`-t`, `--host`, `--exit-code-on-error`, `--html`, `--csv` — see [Running without the web UI](https://docs.locust.io/en/2.46.3/running-without-web-ui.html) and [Configuration](https://docs.locust.io/en/2.46.3/configuration.html).

## Web UI

```bash
make load-locust-ui
```

Opens Locust UI on **:8089**. **Fail-closed** if 8089 is already bound — free the port; do **not** remap app ports (8000/8001/8080/8081).

## Grafana (optional)

Requires `make observability-up` (Alloy :4317/:4318, Grafana :3000). Not §8.1.1 DoD.

```bash
make load-locust-otel   # sets LOAD_LOCUST_OTEL=1 → locust --otel → http://127.0.0.1:4318
```

When `LOAD_LOCUST_OTEL=1`, the smoke script also sets `OTEL_SDK_DISABLED=false` for the Locust process (repo `.env` defaults the SDK off for `billing-api` / load safety). **Fail-closed before Locust:** inspects Compose network `billing-platform` and TCP-reaches Alloy OTLP HTTP `:4318`; exits with `run make observability-up first` if observability is down. Dashboard: Grafana → Billing → **Locust (OTLP)**.

k6 remote-write companion: `make load-k6-grafana` (Compose network → Prometheus; dashboard **k6 Prometheus**).

## Environment

| Variable | Default / fallback | Purpose |
|----------|--------------------|---------|
| `LOAD_HOST` | `BASE_URL` → `http://localhost:8000` | Target API base URL |
| `LOAD_API_KEY` | `K6_API_KEY` | Bearer token |
| `LOAD_ORG_ID` | `K6_ORG_ID` | Organization `public_id` (UUID) |
| `LOAD_FEATURE_KEY` | `K6_FEATURE_KEY` → `api_calls` | Feature key in evaluate/usage bodies |
| `LOAD_USERS` | `5` | Concurrent Locust users |
| `LOAD_SPAWN_RATE` | `1` | Users spawned per second |
| `LOAD_RUN_TIME` | `10s` | Headless duration (`-t`) |
| `LOAD_HTML` | `.local/locust/smoke.html` | HTML report path |
| `LOAD_CSV` | `.local/locust/smoke` | CSV prefix |
| `LOAD_LOCUST_OTEL` | `0` | `1` → `--otel` + OTLP HTTP to Alloy |
| `LOAD_WAIT_MIN` | `0.1` | Min wait between tasks (seconds) |
| `LOAD_WAIT_MAX` | `0.5` | Max wait between tasks (seconds) |

Set `LOAD_WAIT_MIN=0` and `LOAD_WAIT_MAX=0` for ceiling holds (no think time; `constant(0)`). Smoke defaults stay 0.1–0.5.

HTTP bodies use `organization_public_id` / org UUID only — never BIGINT `id`.

## Fail-closed behavior

Preflight **exits nonzero before Locust** when:

| Check | Failure |
|-------|---------|
| Credentials | Missing `LOAD_API_KEY`/`K6_API_KEY` or `LOAD_ORG_ID`/`K6_ORG_ID` |
| API ready | `GET {host}/health/ready` not HTTP 200 |
| Zero requests | Locust quitting hook sets exit code 1 if total requests &lt; 1 |
| Locust errors | `--exit-code-on-error 1` |
| UI port | `make load-locust-ui` fails if :8089 busy |
| OTEL / Alloy | `LOAD_LOCUST_OTEL=1` but network or `:4318` down |

Example (credentials loaded via Make / `.env`):

```bash
LOAD_HOST=http://127.0.0.1:1 make load-locust
# → preflight failed: API ready check failed … Connection refused
```

## User classes (weights)

| Class | Weight | Endpoint |
|-------|--------|----------|
| `EvaluateUser` | 9 | `POST /v1/entitlements/evaluate` |
| `UsageIngestUser` | 4 | `POST /v1/usage/events/batch` |
| `AdminReadUser` | 2 | `GET /v1/organizations/{org}/usage` |

Default **5** users is enough for all three names to appear in a short smoke; **2** users often omit AdminRead.

## Limits (explicit)

- Does **not** replace k6 profiles A–E.
- Does **not** prove profile A **3k RPS** evaluate DoD.
- Laptop / single-replica compose = smoke only.
- Grafana path: `make load-locust-otel` (opt-in; needs observability profile).

## Related

- Smoke evidence: [`docs/perf/locust-smoke-report.md`](../perf/locust-smoke-report.md)
- k6 DoD profiles: [`docs/perf/README.md`](../perf/README.md)
- Observability: [`deploy/observability/README.md`](../../deploy/observability/README.md)
- Compose profiles: [local-compose-profiles.md](local-compose-profiles.md)
- Locust docs: [Quickstart](https://docs.locust.io/en/2.46.3/quickstart.html), [Writing a locustfile](https://docs.locust.io/en/2.46.3/writing-a-locustfile.html), [Running without the web UI](https://docs.locust.io/en/2.46.3/running-without-web-ui.html), [Configuration](https://docs.locust.io/en/2.46.3/configuration.html), [Telemetry / OTEL](https://docs.locust.io/en/stable/telemetry.html)
