# ADR-013: Prometheus / Grafana — Scoped Adopt (LGTP profile)

- **Status:** Accepted (**Adopt scoped** — amended 2026-03-02)
- **Date:** 2026-02-12 (original Defer); amended 2026-03-02
- **Spec:** §8.5.1, §11.3

## Context

Stages 1–2 are sufficient with structlog + OpenTelemetry + documented SLI/alerts (`docs/slo.md`). Stage 3 originally **Deferred** Prometheus/Grafana (Tasks 34–50) pending load evidence.

**Amendment (2026-03-02):** An opt-in Compose profile `observability` adds a **local/demo LGTP stack** (Grafana Alloy + Tempo + Loki + Prometheus + Grafana). Default `make compose-up` remains **without** observability services and with `OTEL_SDK_DISABLED=true`.

| Component | Status |
|-----------|--------|
| OTel meter `billing_platform.slo` + stubs | `src/billing_platform/observability/metrics.py` |
| OTLP traces + metrics (when profile on) | `src/billing_platform/telemetry.py` |
| Alert thresholds + runbook paths | `src/billing_platform/observability/alerts.py` |
| SLI→SLO, metrics, alerts→runbooks | `docs/slo.md` |
| Compose profile `observability` | `deploy/observability/`, `deploy/compose/docker-compose.yml` |
| HTTP `/metrics` scrape endpoint on API | **none** (OTLP → Alloy → Prometheus remote_write) |
| Mimir / HA metrics | **deferred** (single-node Prometheus for local/demo) |
| Kafbat UI | bus only (topics / consumer lag), **not** app SLI |

Load profiles A/C (Tasks 48–49): smoke evidence in `docs/perf/` (**PARTIAL** — laptop smoke A/C; full §8.1.1 RPS requires stand with ≥3 API replicas).

## Historical Defer rationale (until this amendment)

Defer was chosen because: no load A/C evidence at §8.1.1 scale; OTel duplication risk; YAGNI for default dev/kind compose; Console exporter footgun under k6. Those concerns remain for **default** compose — the profile is opt-in only.

## Decision (amended)

**Adopt (scoped)** — LGTP stack behind Compose profile `observability`:

1. **Gateway:** Grafana Alloy — OTLP :4318, tail-based sampling, memory limits, health-span filter.
2. **Traces:** Tempo (local, 48h retention).
3. **Logs:** Loki (72h, low-cardinality labels).
4. **Metrics:** Prometheus single-node (72h retention); OTLP metrics via Alloy remote_write.
5. **UI:** Grafana :3000 (admin/admin local demo only); datasources + Billing SLO dashboard provisioned.
6. **Alerts:** Prometheus rules for OutboxLagHigh / EntitlementLatency with runbook paths in annotations.

**Still deferred / out of scope:**

- Mimir HA, Loki/Tempo object storage (S3/MinIO) — document as production follow-up.
- `GET /metrics` on billing-api — not added; OTLP path only.
- Making observability default on every `compose-up`.
- Claiming full §8.1.1 load DoD on laptop with observability enabled.

### Default compose (unchanged)

- `OTEL_SDK_DISABLED=true` on app services.
- No Alloy/Tempo/Loki/Prometheus/Grafana containers.
- `make load-*` forces `OTEL_SDK_DISABLED=true` on `billing-api`.

### Profile enable

```bash
make observability-up
# or: OTEL_SDK_DISABLED=false OTEL_EXPORTER_OTLP_ENDPOINT=http://alloy:4318 \
#     docker compose -f deploy/compose/docker-compose.yml --profile observability up -d
```

See `deploy/observability/README.md` for retention/sampling tables.

## Consequences

- §11.3 Prom/Grafana bullet: **scoped Adopt** for local/demo profile; not a claim of production SRE platform in Stage3 DoD.
- README / `docs/slo.md` / `AGENTS.md` updated; no false "Prometheus always on" claims.
- Kafbat UI does **not** replace app metrics.
- Production object-storage backends require a future ADR amendment.

## Alternatives considered

- **Full LGTM (Mimir)** — rejected for laptop; documented as Phase-2 production upgrade.
- **Jaeger + Prometheus** — rejected; worse Grafana TraceQL UX vs Tempo.
- **Defer indefinitely** — rejected; local/demo observability path needed for OTLP validation and SLO dashboard wiring.

## Links

- Spec §8.5 / §8.5.1, §11.3
- `deploy/observability/README.md`
- `docs/slo.md`, `src/billing_platform/observability/`
- Runbooks: `docs/runbooks/outbox-lag.md`, `docs/runbooks/entitlement-latency.md`
- Stage 3 plan Task 46; load Tasks 48–49 → `docs/perf/`
