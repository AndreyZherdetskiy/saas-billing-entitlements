# Amendment: ADR-007 Reconciliation (daily cron + ledger↔invoice)

- **Status:** Accepted amendment
- **Date:** 2026-02-11
- **Base ADR:** [007-reconciliation.md](007-reconciliation.md)

## Amendment

1. Stage 2: Celery Beat daily (`0 2 * * *` by default) invokes the same detection engine as manual Admin run.
2. Add discrepancy type **`ledger_invoice_mismatch`** (ledger sums vs invoice totals).
3. On findings — outbox `reconciliation.mismatch` (at-least-once); **forbidden** auto-fix of amounts.
4. Idempotency: run `idempotency_key` includes UTC date (`recon:daily:YYYY-MM-DD`) for cron.

## Links

- Stage 2 plan Tasks 25–26
