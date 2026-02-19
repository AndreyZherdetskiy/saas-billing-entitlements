# Role: Test

Ensure coverage per the `spec.md` §10 pyramid and quality gates §10.4. Required cases — §10.2 (extended by active-stage DoD §11).

## Pyramid

| Layer | Share | Focus |
|-------|-------|--------|
| Unit | ~60% | SM, evaluator, HMAC, outbox payload, ledger reversal, recon mismatch, grace/dunning helpers |
| Integration | ~30% | API + PG/Redis/Kafka (Testcontainers); Helm `helm template`; docker compose config; cross-tenant 403; ready without DB; relay envelope; rate limit 429 |
| E2E | ~10% | billing cycle; failed→grace→dunning→revoke; demo_ui smoke |

## Rules

- Red test first, then code; report exact commands and FAIL/PASS evidence.
- Coverage gate: ≥ **80%** on `services` + `domain`.
- ruff 0, mypy strict 0, integration 100% pass on Testcontainers (`make test-integration`).
- A test is “green” only if it asserts an invariant (e.g. duplicate webhook → no second outbox/ledger row).
- Load / soak (§10.5) — separate target; do not mix into PR unit/integration.
