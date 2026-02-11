# ADR-006: Append-only ledger

- **Status:** Accepted
- **Date:** 2026-02-11
- **Spec:** §4.3.4, §12.8

## Context

Mutating invoice amounts without an auditable history destroys Finance trust and reconciliation accuracy KPI (≥ 99.5%). We need a provable history of platform financial facts.

## Decision

1. Table `ledger_entries` — **INSERT only** from application code.
2. Corrections — compensating entries (`entry_type=reversal`) with `reverses_entry_id` reference.
3. Stage 1 financially significant operations (activation / payment / reversal marker) write ledger in the **same TX** as domain + outbox.
4. Unique `idempotency_key` per entry; dual-id: BIGINT PK + `public_id` UUIDv7 for API.
5. Prefer DB role without UPDATE/DELETE on the table (or deny trigger) — document in migrations/runbook.

## Consequences

- Provable reconciliation and audit trail of financial facts.
- More rows; reversal discipline is mandatory.
- Forbidden: UPDATE amount/status on ledger rows; DELETE "erroneous" entries.

## Alternatives considered

- Mutable invoices only — insufficient for Finance KPI.
- Event sourcing as SoT for entire domain — excessive for stages 1–3.

## Links

- ADR-001 (same TX as outbox), ADR-007 (reconciliation)
