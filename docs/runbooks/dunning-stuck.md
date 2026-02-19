# Runbook: Dunning stuck

**Status:** Stage 2 operational (Tasks 23–24, 26, 32–33; ADR-008 amendment)
**Alert:** `DunningStuck` (`docs/slo.md`)
**Flag:** `DUNNING_ENABLED=true` (API + Celery worker + beat)
**Related metrics (stubs):** `dunning_campaigns_active`, overdue attempts count, campaign age without progress

## Symptoms

- [ ] `dunning_attempts` with `scheduled_at` in the past and status ≠ `completed` / `skipped` > 1 h
- [ ] Subscription in `past_due`, campaign exists, but no new attempts on schedule 1/3/7
- [ ] Celery Beat not enqueueing `dunning.process_due_attempts` (or worker dead)
- [ ] No outbox/Kafka `dunning.*` events on expected step (check [`outbox-lag.md`](outbox-lag.md))
- [ ] CS / demo-ui **Dunning** (`http://localhost:8080/dunning`) — empty with `past_due`, or `paused` without reason

## Quick checks

1. **Flag and workers**
   - [ ] `DUNNING_ENABLED=true` in API **and** `billing-worker` / `billing-beat`
   - [ ] `docker compose -p billing-platform -f deploy/compose/docker-compose.yml ps` — worker and beat healthy; single beat instance
   - [ ] Beat schedule: key `dunning-process-due-attempts` → task `dunning.process_due_attempts` (`workers/beat_schedule.py`)
2. **Campaign (PostgreSQL / Admin API)**
   - [ ] Row in `dunning_campaigns` after webhook `payment_failed`
   - [ ] `GET /v1/admin/dunning/campaigns?organization_public_id=…` (platform_admin / dunning_operator)
   - [ ] `paused_at IS NULL` — if paused, record operator and `reason` (`POST …/pause`)
   - [ ] Subscription status valid (`past_due`; not `canceled` without explicit scenario)
3. **Attempts**
   - [ ] Rows on days 1 / 3 / 7 from `campaign.started_at`
   - [ ] Overdue: `scheduled_at < now()` and status `pending`
   - [ ] Celery logs — no infinite retry without `idempotency_key`
4. **Root cause of past_due**
   - [ ] Webhook path — [`webhook-replay.md`](webhook-replay.md)
   - [ ] Mock Stripe invoice still unpaid (expected until successful payment / `invoice.paid`)

## Safe actions

- [ ] Restart Celery **worker** after checking queue (do not lose in-flight without logs)
- [ ] Restart **beat** only if schedule is consistent (single scheduler)
- [ ] `POST /v1/admin/dunning/campaigns/{id}/pause` for disputed billing (does not mutate ledger)
- [ ] `POST /v1/admin/dunning/campaigns/{id}/resume` after root cause fixed
- [ ] Manual trigger of one attempt — only via idempotent admin/ops path; no raw SQL status flip
- [ ] Do not bypass subscription state machine with direct `UPDATE subscriptions SET status`

## Do not

- [ ] Force `active` without successful payment / webhook
- [ ] `DELETE` attempts / campaigns without audit
- [ ] Globally `DUNNING_ENABLED=false` without change record
- [ ] Edit ledger/invoice amounts “so dunning passes”
- [ ] Publish `dunning.*` from Celery bypassing outbox

## Escalation

| Severity | Condition | Action |
|----------|-----------|--------|
| P3 | delay < 1 h, single tenant | monitor + retry task |
| P2 | attempts stuck > 4 h or >N tenants | eng + Celery/Redis/PG diagnostics |
| P1 | mass involuntary churn risk | RevOps + freeze campaigns (pause) + incident |

## Incident closure checklist

- [ ] Overdue attempts cleared or intentionally skipped
- [ ] Beat/worker healthy
- [ ] Outbox relay not in lag alert
- [ ] Postmortem / task-report entry if code/flag changed

## Related documents

- ADR-008 + `docs/adr/008-dunning-from-stage2-amendment.md`
- Stage 2 plan Tasks 23–24, 26, 33; demo path — `README.md` Stage 2
- [`webhook-replay.md`](webhook-replay.md), [`outbox-lag.md`](outbox-lag.md), [`docs/slo.md`](../slo.md)
- DoD evidence: `.superpowers/sdd/progress.md` (gitignored) + `spec.md` §11.2
