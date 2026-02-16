# SLI / SLO (design targets)

Reference: spec [`spec.md`](../spec.md) §8.5. Single file: **SLI → SLO** + metrics + alerts → runbooks.

These are **engineering design targets** for observability and on-call playbooks — **not** a contractual SLA to customers. External SLA (if any) is a separate commercial document. KPI §1.3 (including webhook processing reliability) is covered by the internal targets below.

Stage 1 establishes metrics and alerts; formal 30-day SLOs start at stage 2+. **ADR-013 (Accepted, scoped Adopt):** Prometheus/Grafana ship via opt-in Compose profile `observability` (LGTP: Alloy + Tempo + Loki + Prometheus + Grafana). Default compose has **no** observability backends and `OTEL_SDK_DISABLED=true`. Mimir / production object storage — deferred.

## OTLP wiring (profile `observability`)

When `make observability-up` (or equivalent compose profile + env):

| Stage | Path |
|-------|------|
| App | OTLP HTTP → base `http://alloy:4318` (Python exporters need full paths — app appends `/v1/traces` and `/v1/metrics`) |
| Gateway | Alloy — filter health, tail sampling (errors, ≥100ms, critical span names, 5% probabilistic) |
| Traces | Tempo (48h) |
| Logs | Loki (72h) — OTLP logs when emitted |
| Metrics | Prometheus (72h) via Alloy remote_write |
| UI | Grafana :3000 — Explore + dashboards **Billing SLO Overview** / **Billing Traces & Service Map** |

Per-process `service.name`: `billing-api`, `billing-worker`, `outbox-relay`, `billing-beat` (`OTEL_SERVICE_NAME`).

Config and retention: [`deploy/observability/README.md`](../deploy/observability/README.md).

## SLIs and SLOs

| SLI | SLO (stage 2+) | KPI link | Stage 1 |
|-----|----------------|----------|---------|
| Share of successful `entitlement.evaluate` (non-5xx) | ≥ 99.9% over 30 days | limit tickets | record metric |
| p99 `entitlement.evaluate` (cached) | < 50 ms | product UX | local benchmarks |
| Share of webhooks in `processed` within 60 s of receive | ≥ 99.9% | MRR leakage / false blocks | DoD: 0 loss after persist |
| `outbox_lag_seconds` p99 | < 5 s | projection freshness / dunning | monitor in Compose |
| Reconciliation accuracy (share of invoices without discrepancy) | ≥ 99.5% per month | Finance trust | manual recon + seed |
| Webhook loss after persist | **0%** | billing reliability | stage 1 invariant |

## Logs

structlog JSON: `timestamp`, `level`, `event`, `correlation_id`, `organization_id`, `duration_ms`.

## Spans (OpenTelemetry)

`http.request`, `db.query`, `redis.command`, `kafka.produce`, `entitlement.evaluate`, `webhook.process`, `outbox.relay.batch`, `reconciliation.run`, `dunning.attempt`.

## Metrics (minimum)

`entitlement_evaluate_total`, `entitlement_evaluate_duration_seconds` (histogram), `entitlement_cache_hit_ratio` (gauge 0–1), `webhook_processing_duration_seconds` (histogram), `outbox_unpublished_count`, `outbox_lag_seconds`, `reconciliation_discrepancy_amount_cents`, `usage_events_ingested_total`, `ledger_entries_posted_total`, `dunning_campaigns_active`, `http_rate_limited_total`.

Code: `src/billing_platform/observability/metrics.py` (OTel meter `billing_platform.slo`; when `OTEL_SDK_DISABLED=true`, record is no-op). Gauges use `create_gauge().set()` (absolute); latencies use histograms. Alert thresholds: `src/billing_platform/observability/alerts.py`.

**Wiring status (post metrics-quality fix 2026-02-16):**

| Helper | Production wiring |
|--------|-------------------|
| `increment_http_rate_limited` | `middleware/rate_limit.py` |
| `increment_entitlement_evaluate` + duration + cache-hit ratio | `services/entitlements.py` `evaluate` |
| `record_webhook_processing_duration_seconds` | `services/webhook_processor.py` |
| `record_outbox_*` | `outbox_relay/publisher.py` each poll |
| `increment_usage_events_ingested` | `services/usage.py` ingest accept |
| `increment_ledger_entries_posted` | `services/ledger.py` new posts |
| `record_reconciliation_discrepancy_amount_cents` | `services/reconciliation.py` run complete |
| `record_dunning_campaigns_active` | `services/dunning.py` start/pause/resume/process_due |
| `should_alert_recon_mismatch` | `services/reconciliation.py` (threshold + metric) |

## Scrape targets / export (stage 3 — scoped Adopt)

ADR-013 **scoped Adopt** — with profile `observability`, OTLP metrics export to Prometheus via Alloy (no `GET /metrics` on API). Prometheus names are typically `billing_platform_slo_<metric>`.

| Metric | Stub helper | Alert | Runtime |
|--------|-------------|-------|---------|
| `outbox_unpublished_count` | `record_outbox_unpublished_count` | OutboxLagHigh (lag) | **wired** |
| `outbox_lag_seconds` | `record_outbox_lag_seconds` | OutboxLagHigh | **wired** |
| `reconciliation_discrepancy_amount_cents` | `record_reconciliation_discrepancy_amount_cents` | ReconMismatch | **wired** |
| `entitlement_evaluate_total` | `increment_entitlement_evaluate` | — | **wired** |
| `entitlement_evaluate_duration_seconds` | `record_entitlement_evaluate_duration_seconds` | EntitlementLatency | **wired** |
| `entitlement_cache_hit_ratio` | `record_entitlement_cache_hit_ratio` | — | **wired** (rolling) |
| `webhook_processing_duration_seconds` | `record_webhook_processing_duration_seconds` | — | **wired** |
| `usage_events_ingested_total` | `increment_usage_events_ingested` | — | **wired** |
| `ledger_entries_posted_total` | `increment_ledger_entries_posted` | — | **wired** |
| `dunning_campaigns_active` | `record_dunning_campaigns_active` | DunningStuck (docs; Prom rule deferred) | **wired** |
| `http_rate_limited_total` | `increment_http_rate_limited` | — | **wired** |

WebhookFailRate alert uses Tempo spanmetrics on `webhook.process` (see `deploy/observability/prometheus/alerts.yml`). ReadyProbeFail remains runbook-only until blackbox probes.

## Alerts → runbooks

| Alert | Condition | Priority | Runbook |
|-------|-----------|----------|---------|
| OutboxLagHigh | `outbox_lag_seconds` > 300 | P2 | [`docs/runbooks/outbox-lag.md`](runbooks/outbox-lag.md) |
| WebhookFailRate | failed_rate > 1% over 15 min | P2 | [`docs/runbooks/webhook-replay.md`](runbooks/webhook-replay.md) |
| ReconMismatch | discrepancy amount > $100 | P3 | [`docs/runbooks/reconciliation-mismatch.md`](runbooks/reconciliation-mismatch.md) |
| EntitlementLatency | p99 > 100 ms for 5 min | P3 | [`docs/runbooks/entitlement-latency.md`](runbooks/entitlement-latency.md) (stub) |
| ReadyProbeFail | ready fails > 2 min | P1 | [`docs/runbooks/ready-probe-fail.md`](runbooks/ready-probe-fail.md) (stub) |
| DunningStuck | attempt overdue > 1 h (stage 2) | P3 | [`docs/runbooks/dunning-stuck.md`](runbooks/dunning-stuck.md) |

## Incident response template

Symptoms → check metrics/logs (`correlation_id`) → safe actions (webhook replay, pause dunning, scale relay) → escalation → postmortem for P1/P2.

Do not mutate `ledger_entries` or publish domain facts outside the outbox.
