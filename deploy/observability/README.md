# LGTP observability stack (opt-in Compose profile)

Local/demo observability: **Grafana Alloy** (OTLP gateway) → **Tempo** (traces) + **Loki** (logs) + **Prometheus** (metrics) → **Grafana** UI.

**Not started by default** — `make compose-up` keeps `OTEL_SDK_DISABLED=true`. Enable with `make observability-up` or:

```bash
OTEL_SDK_DISABLED=false OTEL_EXPORTER_OTLP_ENDPOINT=http://alloy:4318 \
  docker compose -p billing-platform -f deploy/compose/docker-compose.yml --profile observability up -d --build
```

Grafana: http://localhost:3000 (admin / admin — **local demo only**).

## Retention and sampling

| Signal | Backend | Retention | Notes |
|--------|---------|-----------|-------|
| Traces | Tempo | **48h** | Local filesystem; compaction enabled |
| Logs | Loki | **72h** | Low-cardinality labels only (`service_name`, `level`, `deployment_environment`) |
| Metrics | Prometheus | **72h** | OTLP → Alloy → remote_write; scrape interval 15s |

### Tail-based sampling (Alloy)

Decision after trace completes (`decision_wait` 8s). Policies (OR — keep if any matches):

| Priority | Policy | Rule |
|----------|--------|------|
| pre-filter | `filter` | Drop `/health/live`, `/health/ready` spans |
| 1 | `status_code` | Keep `ERROR` |
| 2 | `latency` | Keep duration ≥ **100 ms** |
| 3 | `ottl_condition` | Keep critical span names (`webhook.process`, `outbox.relay.batch`, `reconciliation.run`, `dunning.attempt`) |
| 4 | `probabilistic` | **5%** of remaining success traces |

Quiet Tempo after evaluate-only success at 5% sampling is **expected**.

## Service graph (Grafana)

Tempo **metrics-generator** derives `traces_service_graph_*` / span metrics and **remote_writes** them to Prometheus (`http://prometheus:9090/api/v1/write`). Grafana Tempo datasource already points `serviceMap` at Prometheus.

- Dashboard **Billing → Billing SLO Overview**: panels **Error traces**, **Service graph** (node graph), **Service graph edge rate**.
- Or **Explore → Tempo → Service Graph** tab.

Edges appear when traces include **client → server** (or messaging) span pairs across `service.name` values. Single orphan ERROR spans do not draw a map by themselves.

## App env (profile on)

| Variable | Example | Notes |
|----------|---------|-------|
| `OTEL_SDK_DISABLED` | `false` | Required to emit signals |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://alloy:4318` | Base OTLP HTTP URL; app appends `/v1/traces` and `/v1/metrics` |
| `OTEL_SERVICE_NAME` | `billing-api` | Per process; compose sets defaults |

Per-service defaults in compose: `billing-api`, `billing-worker`, `outbox-relay`, `billing-beat`.

## Load testing safety (G5)

`make load-*` forces `OTEL_SDK_DISABLED=true` on `billing-api` — do **not** change that default.

Optional load with observability (validates OTLP path; **not** a merge gate — smoke A remains PARTIAL on constrained hosts with or without OTEL):

```bash
make observability-up
LOAD_OTEL_SDK_DISABLED=false OTEL_EXPORTER_OTLP_ENDPOINT=http://alloy:4318 make load-a
```

### Locust OTEL + k6 Prometheus remote write (visualization)

Grafana: http://localhost:3000 (admin / admin — local demo only). Folder **Billing**:

| Dashboard | Source | How to populate |
|-----------|--------|-----------------|
| **Locust (OTLP)** | Locust `locust[otel]` + `--otel` → Alloy OTLP HTTP :4318 | `make load-locust-otel` |
| **k6 Prometheus** | Official dashboard 19665 (Counter/Gauge trends; not native histograms) | `make load-k6-grafana` |

```bash
make observability-up
make load-locust-otel
make load-k6-grafana
```

Default `make load-a`…`load-e` and `make load-locust` stay Grafana-free. Prometheus :9090 is **not** published on the host — k6 RW uses Compose network `billing-platform` → `http://prometheus:9090/api/v1/write`.

**Fail-closed:** `make load-locust-otel` checks the Compose network and TCP `:4318` before Locust starts. `make load-k6-grafana` checks the network before k6 starts. Both exit with `run make observability-up first` when observability is down.

**k6 script delivery:** `load_k6_grafana.sh` uses `k6 run -` (stdin from host `docs/perf/*.js`), not a bind mount. Docker Desktop on WSL often presents an empty `docs/perf` mount inside the container (same class of issue as baked observability configs under `deploy/observability/`).

See [`docs/perf/README.md`](../../docs/perf/README.md).

## Files

| File | Role |
|------|------|
| `alloy.alloy` | OTLP gateway, filter, memory_limiter, batch, tail_sampling, exporters |
| `tempo.yaml` | Trace storage (48h) |
| `loki.yaml` | Log storage (72h) |
| `prometheus.yml` | Scrape + alert rules |
| `prometheus/alerts.yml` | OutboxLagHigh, EntitlementLatency, ReconMismatch, WebhookFailRate |
| `grafana/provisioning/` | Datasources + dashboards |
| `grafana/provisioning/dashboards/billing-slo-overview.json` | App SLO gauges/histograms + RED + errors + service graph |
| `grafana/provisioning/dashboards/billing-traces-service-map.json` | Service map deep-dive + TraceQL error panels |
| `grafana/provisioning/dashboards/locust-otel.json` | Locust OTLP metrics (job=locust) |
| `grafana/provisioning/dashboards/k6-prometheus.json` | Official k6 Prometheus dashboard 19665 |

App SLO helpers live in `src/billing_platform/observability/metrics.py` (gauges via `set`, latencies as histograms). Wiring status: [`docs/slo.md`](../../docs/slo.md).

## Grafana dashboards

After `make observability-up` (admin / admin — local demo only):

| Folder | Dashboard | Purpose |
|--------|-----------|---------|
| Billing | **Billing SLO Overview** | Stats (rate / error / p99 / failed edges), SLO stubs, RED charts, error traces, service graph |
| Billing | **Billing Traces & Service Map** | Large service map, ERROR / critical TraceQL, top spans & edges |
| Billing | **Locust (OTLP)** | Locust `--otel` request rate / errors / latency / users |
| Billing | **k6 Prometheus** | k6 OSS results via Prometheus remote write (dashboard 19665) |

Filter **service** on the SLO overview (variable from `traces_spanmetrics_calls_total`). Refresh dashboards with Ctrl+F5 after compose recreate if panels look stale.

- Tempo/Loki object storage (S3/MinIO)
- Mimir HA for metrics
- Alloy clustering for multi-replica tail sampling
