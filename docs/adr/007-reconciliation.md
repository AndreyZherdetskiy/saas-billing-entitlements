# ADR-007: Reconciliation as first-class

- **Status:** Accepted (stage 1 — manual; stage 2 — daily cron)
- **Date:** 2026-02-11
- **Spec:** §12.8, API G

## Context

Webhooks are lost, duplicated, and arrive out-of-order. Reconciliation accuracy KPI ≥ 99.5% requires an explicit subsystem for registry comparison, not ad-hoc SQL at month end.

## Decision

1. Reconciliation is first-class: `reconciliation_runs` + `reconciliation_discrepancies`.
2. **Stage 1:** manual `POST /admin/reconciliation/run` + seed mismatch for demo.
3. **Stage 2:** daily cron (Celery), alerts on `RECONCILIATION_ALERT_AMOUNT_CENTS`.
4. Re-run does **not** mutate invoices/ledger; discrepancies are detection facts; remediation — runbook + replay + compensating ledger entry.
5. Discrepancy types: missing_in_platform, amount_mismatch, status_mismatch, missing_in_stripe, ledger_invoice_mismatch.

## Consequences

- Detects gaps; does not always auto-fix.
- Runbook `reconciliation-mismatch` required.
- Forbidden: silently UPDATE amounts to "fix" history.

## Alternatives considered

- Trust webhooks only — does not meet KPI.
- Streaming Flink join — excessive for stages 1–2.

## Links

- ADR-005 (mock Stripe registry), ADR-006 (ledger), `docs/runbooks/reconciliation-mismatch.md`
