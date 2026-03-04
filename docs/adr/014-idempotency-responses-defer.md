# ADR-014: HTTP `idempotency_responses` — Defer (post–Stage 3)

- **Status:** Accepted (**Defer**)
- **Date:** 2026-02-12
- **Spec:** §6.4, §10

## Context

`spec.md` §6.4 (stage 2) proposed table `idempotency_responses` to replay the **same HTTP response** on repeated `Idempotency-Key` for mutating POST. After Stage 3 in the repository:

| Mechanism | Status |
|----------|--------|
| `Idempotency-Key` on mutating POST (API contract) | Required per Spec |
| Domain idempotency (usage `idempotency_key`, webhook `provider_event_id`, ledger/outbox keys) | Implemented |
| Table `idempotency_responses` + replay of stored HTTP body/status | **Not implemented** |

Repeated POST with the same key today may **re-execute** the handler if the domain layer does not deduplicate the operation. Critical paths (usage, webhooks, ledger, outbox) are covered by business keys; gap — generic HTTP replay for other POST.

## Decision

**Defer** — do not introduce `idempotency_responses` in stages 1–3 scope.

1. **Retained:** `Idempotency-Key` header on mutating POST; usage `idempotency_key` + webhook `provider_event_id` — permanent unique constraints (§6.4).
2. **Deferred:** migration/model `idempotency_responses`, middleware "return stored response" for arbitrary POST.
3. **Review:** amendment or new ADR on product requirement for "exact HTTP replay" for SDK clients / public API outside already-deduplicated domain operations.

## Consequences

- §6.4 in `spec.md` updated: HTTP response replay — roadmap, not Stage 3 DoD.
- Agents must not add `idempotency_responses` "by default" without explicit brief / amendment.
- Clients should rely on domain idempotency (usage keys, webhook ids) and unique constraints in §6.4.
- Risk: repeated POST without domain dedupe may have side effect — mitigated by integration tests + explicit unique keys on subscriptions/invoices/recon.

## Alternatives considered

- **Implement now** — rejected: YAGNI; domain keys cover critical paths; response storage/TTL/cleanup — separate product scope.
- **Remove `Idempotency-Key` from contract** — rejected: contract remains; replay storage deferred.

## Links

- Spec §6.4, §10.2
- ADR-001 (outbox idempotency_key), ADR-010 (dual-id)
- Architecture audit B5-01
