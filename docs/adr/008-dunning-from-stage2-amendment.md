# Amendment: ADR-008 Dunning (stage 2 activation)

- **Status:** Accepted amendment
- **Date:** 2026-02-11
- **Base ADR:** [008-dunning-from-stage2.md](008-dunning-from-stage2.md)

## Amendment

1. Stage 2 **enables** full cycle when `DUNNING_ENABLED=true`.
2. Campaign created from webhook path `invoice.payment_failed` (idempotent key).
3. Attempt schedule: **day 1 / 3 / 7** from `campaign.started_at` (spec §4.3.7).
4. Admin pause/resume (`dunning_operator` or `platform_admin`); pause does **not** delete attempts and does **not** mutate ledger.
5. Attempt side effects: mock Stripe retry + outbox `dunning.*` events (ESP — external consumer).
6. Runbook `dunning-stuck.md` must be completed in Task 33.

## Links

- Stage 2 plan Tasks 23–24, 33
