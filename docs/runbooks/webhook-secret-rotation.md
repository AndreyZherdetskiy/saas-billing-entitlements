# Runbook: Webhook secret rotation (overlap)

**Status:** Task 42 operational
**Symptom / trigger:** planned rotation of `MOCK_STRIPE_WEBHOOK_SECRET`; mass `signature mismatch` after provider rotation

## Goal

Rotate signing secret without downtime: API accepts signature with **current** (`MOCK_STRIPE_WEBHOOK_SECRET`) **or previous** (`MOCK_STRIPE_WEBHOOK_SECRET_PREVIOUS`) secret. Verify errors do not reveal which secret failed.

## Procedure (zero-downtime)

1. **Prepare:** generate new secret (Secrets Manager / vault); do not commit to git.
2. **Overlap:** deploy with:
   - `MOCK_STRIPE_WEBHOOK_SECRET` = **new** secret;
   - `MOCK_STRIPE_WEBHOOK_SECRET_PREVIOUS` = **old** (current before this step).
3. **Provider:** update webhook signing secret at mock Stripe / Stripe to **new**; in-flight deliveries with old secret still pass verify.
4. **Soak:** ≥ `WEBHOOK_TIMESTAMP_TOLERANCE_SECONDS` (default 300 s) + buffer for provider retries (recommend 24–48 h).
5. **Finish:** remove `MOCK_STRIPE_WEBHOOK_SECRET_PREVIOUS` (empty value → current only).
6. **Verify:** test webhook with new secret → `200`; with old secret after step 5 → `400 signature mismatch`.

## Quick checks

- [ ] Both keys set only in runtime secrets (Helm/K8s Secret, compose `.env` locally)
- [ ] `billing-api` pod restarted after env change
- [ ] Clock skew normal (`WEBHOOK_TIMESTAMP_TOLERANCE_SECONDS`)
- [ ] No spike in `WebhookFailRate` / `webhook_events.status = failed` with `signature mismatch`

## Do not

- [ ] Disable HMAC verify in non-dev
- [ ] Store secrets in git / values with real values
- [ ] Leave `PREVIOUS` forever without need (widens compromise window for old secret)

## Rollback

If new secret is wrong: restore `MOCK_STRIPE_WEBHOOK_SECRET` to old, clear `PREVIOUS`, revert secret at provider; replay failed webhooks — see [webhook-replay.md](webhook-replay.md).

## Related documents

- ADR-005 (PaymentProviderPort + HMAC)
- [webhook-replay.md](webhook-replay.md)
- `spec.md` §8 (webhook secrets)
