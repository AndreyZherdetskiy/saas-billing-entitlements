# ADR-008: Dunning from stage 2

- **Status:** Accepted (scope gate)
- **Date:** 2026-02-11
- **Spec:** §12.9, §3.3–3.4

## Context

Full dunning (campaigns, attempts, pause, notifier) without a reliable webhook/outbox/entitlements domain bloats the MVP. Stage 1 must prove persist, outbox, entitlements, and reconciliation scaffolding.

## Decision

1. **Stage 1:** domain events only `subscription.payment_failed` / `subscription.past_due` (+ related invoice events); `DUNNING_ENABLED=false`.
2. **Stage 2:** `dunning_campaigns` / `dunning_attempts`, attempt schedule, operator pause, notifier events; Celery steps.
3. Dunning tables are **not** in the mandatory stage 1 baseline (empty stubs allowed only if non-blocking).
4. Runbook `dunning-stuck.md` — stub until stage 2.

## Consequences

- Stage 1 reduces involuntary churn partially (grace/revoke correctness).
- Forbidden: pull full ESP/email templates into stage 1.

## Alternatives considered

- Full dunning in MVP — scope bloat.
- External ESP as part of Platform — outside responsibility boundary.

## Links

- Spec §3.4, ADR-004 (Celery for dunning steps)
