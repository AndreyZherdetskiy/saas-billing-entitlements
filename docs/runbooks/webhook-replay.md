# Runbook: Webhook replay

**Status:** Task 6–7 + E4-01 admin replay implemented (persist-first + idempotent processor)
**Alert:** WebhookFailRate

## Symptoms

- [ ] `webhook_events.status = failed` (or stuck in `processing`)
- [ ] Subscription did not transition to `active` after payment in mock Stripe / provider
- [ ] Ledger / outbox missing after expected `invoice.paid`
- [ ] Provider redeliveries with no effect (or duplicate storm in logs)

## Quick checks

- [ ] **Rate limit:** `POST /v1/webhooks/*` does not go through the Redis API-key rate limiter — HMAC only. For abuse, check edge/WAF / ingress throttling (in-app IP limiter not implemented).
- [ ] HMAC / `Stripe-Signature` and `WEBHOOK_TIMESTAMP_TOLERANCE_SECONDS` (±300 s default)
- [ ] After recent rotation — `MOCK_STRIPE_WEBHOOK_SECRET_PREVIOUS` and overlap window; see [webhook-secret-rotation.md](webhook-secret-rotation.md)
- [ ] `provider_event_id` / event `id` — duplicate must be no-op (no second outbox/ledger)
- [ ] **mock-stripe test emitter:** `POST /v1/test/emit-webhook` on mock-stripe (`:8001`) generates a **new** `provider_event_id` each call. Repeating the same invoice JSON creates a new webhook row; ledger/outbox stay stable, but the row may land in `failed` (e.g. illegal `active → active`) instead of `processed`. This is not silent double-processing — true idempotency is per persisted `provider_event_id`.
- [ ] `last_error`, `processing_attempts` in `webhook_events`
- [ ] `external_subscription_id` on subscription matches payload `data.object.subscription`
- [ ] `/health/ready` and PostgreSQL availability
- [ ] `correlation_id` = webhook id in ledger metadata

## Safe actions

- [ ] Fix secret config / clock skew → redeliver from mock-stripe:
  ```bash
  curl -X POST http://localhost:8001/v1/test/emit-webhook \
    -H 'Content-Type: application/json' \
    -d '{"event_type":"invoice.paid","data":{...}}'
  ```
- [ ] Replay failed webhook: `POST /v1/admin/webhooks/{id}/replay` (Bearer `platform_admin` API key) — idempotent processing; replay for `processed`/`skipped` → `already_processed` without second ledger/outbox
- [ ] Poison payload: mark `failed`, fix data, replay with new event (no ledger UPDATE)
- [ ] Check outbox lag if events do not reach Kafka — see `outbox-lag.md`

## Do not

- [ ] Disable signature verify in non-dev
- [ ] UPDATE / DELETE `ledger_entries` rows “to make it match”
- [ ] Manual INSERT into outbox bypassing `enqueue_outbox` + domain transaction
- [ ] Change `subscription.status` directly in SQL

## Escalation

| Severity | Condition | Action |
|----------|-----------|--------|
| P3 | single failed, known cause | replay after fix |
| P2 | systemic failed / missing external_subscription_id | eng + data fix |
| P1 | mass webhook loss | incident; persist-first audit |

## Related documents

- ADR-005 (PaymentProviderPort + mock Stripe)
- `docs/runbooks/webhook-secret-rotation.md`, `docs/runbooks/outbox-lag.md`, `docs/runbooks/reconciliation-mismatch.md`
