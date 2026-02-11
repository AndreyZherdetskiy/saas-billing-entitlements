# ADR-010: Identifier policy (UUIDv7, dual-id, outbox BIGINT)

- **Status:** Accepted
- **Date:** 2026-02-11
- **Spec:** §6.2, §12.14

## Context

We need a unified PK/FK policy: hot tables are written frequently, API must not expose sequential ids, and catalog/operational journals should not carry unnecessary dual-id.

## Decision

1. **UUIDv7** — default surrogate and `public_id` value (time-ordered). UUIDv4 as PK of frequently written tables forbidden without separate ADR; v4 — only for opaque secrets/tokens.
2. **Dual-id** only on: `organizations`, `subscriptions`, `invoices`, `usage_events`, public `ledger_entries`: `BIGINT GENERATED AS IDENTITY` PK + `public_id` UUIDv7 UNIQUE. FK inside DB — on BIGINT.
3. **Catalog** (`products`/`plans`/`prices`/`features`), webhooks, reconciliation, dunning — single-column **UUIDv7 PK**.
4. **`plan_features`:** surrogate UUIDv7 PK + `UNIQUE (plan_id, feature_id)` (not composite PK — lifecycle attributes exist).
5. **`outbox_messages`:** BIGINT PK **without** `public_id`; Kafka message key = `id`.
6. API/OpenAPI/paths/events externally — only `public_id` / UUID; public→internal mapping at service boundary.

## Consequences

- Better index locality than UUIDv4; API does not expose sequence.
- BIGINT leak risk on erroneous serialization — caught by review Gate C and Pydantic schemas.
- Forbidden: dual-id on all tables "just in case"; `(organization_id, id)` as composite PK for multi-tenant.

## Alternatives considered

- UUIDv4 everywhere — simpler start, worse locality.
- Dual-id everywhere — bloats ORM/events.
- Composite tenant PK — complicates FK and outbox aggregate_id.

## Links

- ADR-001 (outbox id as Kafka key), Spec §6.2–6.3
