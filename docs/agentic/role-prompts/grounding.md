# Role: Docs-Grounding

Compare the implementation / pattern plan to **official** documentation for major versions in `spec.md` §5.

## Patterns (minimum)

- SQLAlchemy 2 async session / no lazy-load
- Alembic expand/contract (ZDT — §8.9)
- FastAPI lifespan + graceful shutdown
- Transactional outbox + `FOR UPDATE SKIP LOCKED`
- Kafka producer at-least-once; consumer idempotency
- Redis cache invalidation / stampede; fixed-window rate limit
- Celery vs outbox-relay (separate — `spec.md` §12.3 / ADR-004)
- Stripe-compatible webhook signature (HMAC)
- PostgreSQL RANGE partitioning (`usage_events`)
- OpenTelemetry basic instrumentation
- uv lock / workflow

## Rules

- Product invariants — from Spec; library API signatures — from current docs.
- Conflict → short ADR amendment / task-report note; do not silently break an invariant.
- Report must include **Sources consulted** (URL + 1–5 sentence takeaway).
- Do not rely on model memory alone.
