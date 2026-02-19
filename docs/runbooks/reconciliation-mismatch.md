# Runbook: Reconciliation mismatch

**Status:** Task 11 implemented (manual run + seed script)
**Alert:** ReconMismatch (amount > `RECONCILIATION_ALERT_AMOUNT_CENTS`, default $100)
**Metric (stub):** `reconciliation_discrepancy_amount_cents` — `observability/metrics.py`; threshold — `should_alert_recon_mismatch` / `alerts.py`

## Symptoms

- [ ] Rows in `reconciliation_discrepancies` after manual or daily run.
- Platform ledger (`metadata.invoice_external_id`, `amount_cents`) ↔ mock Stripe registry (`amount_due` / `amount_paid`) mismatch.
- Kafka `reconciliation.mismatch` when delta ≥ threshold.

## Quick checks

1. **Latest run:** `GET /v1/admin/reconciliation/runs` (platform_admin) or SQL:
   ```sql
   SELECT id, run_type, status, stats, started_at, completed_at
   FROM reconciliation_runs ORDER BY started_at DESC LIMIT 5;
   ```
2. **Discrepancies:** `GET /v1/admin/reconciliation/runs/{run_id}/discrepancies` or:
   ```sql
   SELECT kind, external_invoice_id, expected_amount_cents, actual_amount_cents, delta_cents, details
   FROM reconciliation_discrepancies WHERE run_id = '<uuid>' ORDER BY created_at;
   ```
3. **Discrepancy type:**
   - `amount_mismatch` — Stripe amount ≠ ledger
   - `status_mismatch` — invoice status differs
   - `missing_in_platform` — invoice in Stripe, no ledger
   - `missing_in_stripe` — ledger exists, invoice not in registry
   - `ledger_invoice_mismatch` — ledger aggregate vs invoice (stage 2+)
4. **Timeline:** `correlation_id` in ledger, `webhook_events` by invoice id, mock Stripe `GET /v1/invoices`.

## Demo seed (local)

```bash
uv run python scripts/seed_recon_mismatch.py
# then POST /v1/admin/reconciliation/run with Idempotency-Key
```

## Safe actions

- **Re-run** — creates new `reconciliation_run` (new `Idempotency-Key`); does **not** mutate invoices/ledger.
- **Replay webhook** for `missing_in_platform` — see `docs/runbooks/webhook-replay.md`.
- **Compensating ledger reversal** for confirmed posting error — `LedgerService.reverse` (append-only).
- **Manual remediation** — runbook + replay + compensating entry; discrepancies remain as audit trail.

## Do not

- Silent `UPDATE` of invoice/ledger amounts to “align” reconciliation.
- Delete discrepancy without investigation and audit record.
- Auto-fix ledger amounts in reconciliation code (ADR-007 Gate D).

## Escalation

| Severity | Condition | Action |
|----------|-----------|--------|
| P3 | delta > $100 | RevOps review within 1 business day |
| P2 | systemic missing_in_platform | check webhook pipeline + outbox lag |
| P1 | material mismatch > $10k | freeze manual credits; finance + eng |

## Related documents

- ADR-007, `docs/slo.md` (ReconMismatch alert)
- `docs/runbooks/webhook-replay.md`, `docs/runbooks/outbox-lag.md`
