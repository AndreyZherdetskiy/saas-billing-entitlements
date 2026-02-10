# Amendment: ADR-004 — multi-replica outbox-relay

- **Status:** Accepted amendment (stage 3 plan; code — Task 39)
- **Date:** 2026-02-10
- **Base ADR:** [004-celery-vs-outbox-relay.md](004-celery-vs-outbox-relay.md)

## Amendment

1. Stage 3: **≥2 replicas** of the `outbox-relay` process are allowed.
2. Claim batch: `SELECT … FOR UPDATE SKIP LOCKED` (or equivalent) so two replicas do not claim the same row.
3. Publishing remains at-least-once; consumer idempotency + uniqueness of `idempotency_key` / Kafka message key = outbox id prevent a "double fact" in the sense of §11.3.
4. Celery still does **not** publish domain facts to Kafka.

## Links

- Spec §11.3; stage 3 plan Task 39; ADR-001
