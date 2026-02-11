# ADR-005: PaymentProviderPort and mock Stripe

- **Status:** Accepted
- **Date:** 2026-02-11
- **Spec:** §5.1, §12.5

## Context

We need local reproducibility of billing patterns (webhooks, reconciliation, signature) without PCI, a billing account, and the slow feedback loop of live Stripe.

## Decision

1. Domain depends only on **`PaymentProviderPort`** (Protocol): create_customer, create_subscription, cancel_subscription (stage 1 minimum).
2. Stage 1 implementation — HTTP client to the **`mock-stripe`** service (Compose).
3. Webhooks: Stripe-compatible HMAC (`Stripe-Signature`), secret `MOCK_STRIPE_WEBHOOK_SECRET`, ±5 min tolerance, constant-time compare.
4. Persist-first: `webhook_events` INSERT … ON CONFLICT (`provider_event_id`) DO NOTHING before business processing.
5. Domain does **not** import the official Stripe SDK. Live Stripe — swap behind Port in stage 3+.

## Consequences

- Demo and tests without PCI; port contract covered by tests for future swap.
- Mock may not cover all live Stripe edge cases — document gaps in port tests.
- Forbidden: store PAN; accept webhooks without verify in non-dev.

## Alternatives considered

- Manual webhook fixtures without provider — weak reconciliation story.
- Live Stripe from day one — secrets, slow loop, PCI distraction for MVP scope.

## Links

- ADR-007 (reconciliation vs mock registry), Spec §7.4
