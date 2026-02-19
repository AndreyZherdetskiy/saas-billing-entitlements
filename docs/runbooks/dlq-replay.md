# Runbook: Outbox DLQ replay

**Status:** operational (Task 40)
**Symptom:** growing `outbox_dead_letters`; outbox rows with `publish_attempts` ≥ `OUTBOX_MAX_ATTEMPTS` and `published_at` set (poison path)

## Symptoms

- [ ] Metric / alert on failed publish or DLQ growth
- [ ] In `outbox_messages`: `published_at IS NOT NULL`, but event did not reach consumers (poison)
- [ ] New rows in `outbox_dead_letters` with `last_error`
- [ ] Relay logs repeating Kafka errors / “organization not found”

## RBAC and access (ops)

`scripts/replay_outbox_dlq.py` — **CLI with direct `DATABASE_URL`**, not HTTP Admin API.

| Aspect | Policy |
|--------|----------|
| API RBAC | `platform_admin` **does not apply** — script bypasses API |
| Who can replay | Ops / eng with **primary DB credentials** only (break-glass, CI secret, local dev) |
| Audit | JSON on script stdout; save in ticket/postmortem |
| Alternative | Future admin endpoint (see webhook-replay runbook) — does not replace DLQ path without separate ADR |

Do not pass prod `DATABASE_URL` to laptops without policy; do not run replay from untrusted CI jobs without review.

## Quick checks

- [ ] Poison payload vs transient Kafka outage (temporary outage — restart relay, see [outbox-lag.md](outbox-lag.md))
- [ ] `correlation_id` / `outbox_message_id` / `outbox_dead_letters.id`
- [ ] `replayed_at` on DLQ — already replayed? (repeat replay is idempotent, status `already_replayed`)
- [ ] Root cause fixed (Kafka, org missing, invalid payload)

## Safe actions

1. Find DLQ id:
   ```sql
   SELECT id, outbox_message_id, event_type, last_error, moved_at, replayed_at
   FROM outbox_dead_letters
   ORDER BY moved_at DESC
   LIMIT 20;
   ```
2. Dry-run:
   ```bash
   DATABASE_URL=postgresql+asyncpg://billing:billing@localhost:5432/billing \
     uv run python scripts/replay_outbox_dlq.py --dlq-id <ID> --dry-run
   ```
3. Replay (after root cause fix):
   ```bash
   DATABASE_URL=postgresql+asyncpg://billing:billing@localhost:5432/billing \
     uv run python scripts/replay_outbox_dlq.py --dlq-id <ID>
   ```
4. Script writes JSON audit to stdout; relay picks up outbox row (`published_at=NULL`).
5. Repeat `--dlq-id` → `already_replayed` (no duplicate pending outbox).

## Do not

- [ ] Publish to Kafka directly (dual-write forbidden — ADR-001)
- [ ] DELETE from `outbox_messages` / `outbox_dead_letters` without audit / postmortem
- [ ] UPDATE `ledger_entries` (append-only — ADR-006)
- [ ] INSERT new outbox row instead of resetting existing (duplicate event_id / consumers)

## Idempotency

- Column `outbox_dead_letters.replayed_at` (expand-only migration `20260216_0018`, ADR-009).
- First replay: reset outbox + `replayed_at=now()`.
- Repeat same `dlq_id`: skip, outbox untouched.

## Escalation

| Severity | Condition | Action |
|----------|-----------|--------|
| P3 | single poison after deploy | fix + replay |
| P2 | DLQ growing in batches | eng + root cause |
| P1 | Kafka/PG unavailable | incident; no replay until stable |

## Related documents

- ADR-001 (transactional outbox), ADR-004 (relay), ADR-009 (expand-only migrations)
- [outbox-lag.md](outbox-lag.md)
- Stage 3 plan Task 40
