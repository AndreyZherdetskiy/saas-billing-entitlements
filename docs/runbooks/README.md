# Runbooks

On-call / local debugging playbooks. Alert canon: [`docs/slo.md`](../slo.md) / spec §8.5.

Full texts for the four DoD playbooks; **EntitlementLatency** and **ReadyProbeFail** are short stubs (spec gave inline guidance).

| Runbook | Alert / symptom | Status | Link |
|---------|-----------------|--------|------|
| [outbox-lag.md](outbox-lag.md) | high `outbox_lag_seconds`; rising `outbox_unpublished_count` | operational | ADR-001 |
| [webhook-replay.md](webhook-replay.md) | webhook `failed`; duplicate storms; need replay | operational | ADR-005 |
| [webhook-secret-rotation.md](webhook-secret-rotation.md) | planned signing secret rotation | operational (S3) | Task 42 / §11.3 |
| [reconciliation-mismatch.md](reconciliation-mismatch.md) | discrepancy amount / count | operational | ADR-007 |
| [dunning-stuck.md](dunning-stuck.md) | attempt overdue (stage 2+) | operational | ADR-008 |
| [entitlement-latency.md](entitlement-latency.md) | EntitlementLatency p99 | **stub** | ADR-003 |
| [ready-probe-fail.md](ready-probe-fail.md) | ReadyProbeFail | **stub** | §8.6 |
| [helm-kind-smoke.md](helm-kind-smoke.md) | Helm chart smoke kind/minikube | operational (S3) | §11.3 |
| [migration-zdt-usage.md](migration-zdt-usage.md) | ZDT expand drill on hot table (`invoices`) | operational | ADR-009 |
| [replica-lag.md](replica-lag.md) | replica lag / stale evaluate | operational (S3) | ADR-003 amend |
| [pgbouncer-pools.md](pgbouncer-pools.md) | connection pooling / too many clients | operational (S3) | §8.1 |
| [local-compose-profiles.md](local-compose-profiles.md) | Compose profiles / local DSN | operational | §11.3 |
| [dlq-replay.md](dlq-replay.md) | outbox DLQ replay | operational (S3) | ADR-001 |
| [load-locust.md](load-locust.md) | Locust smoke / UI (:8089); not profile A 3k RPS DoD | operational | §8.1.1 additive |

## Runbook content template

1. **Symptoms** (metrics, logs, UX).
2. **Quick checks** (ready, lag, DLQ, correlation_id).
3. **Safe actions** (without breaking ledger/outbox invariants).
4. **Escalation**.
5. **Postmortem** (P1/P2).

Do not mutate `ledger_entries` or “fix” history with silent UPDATE. Compensating entry / replay are preferred actions.
