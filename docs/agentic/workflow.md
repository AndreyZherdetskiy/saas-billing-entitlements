# Agentic workflow (local)

Task execution loop for Cursor + Superpowers.

**Entry point:** [`AGENTS.md`](../../AGENTS.md) (required; `docs/` map and sync — §0).
Product source of truth: [`spec.md`](../../spec.md).

**Phases and prompts:** via [`AGENTS.md` §10](../../AGENTS.md#10-stage-development-supplement) only (§10.1 — how to run; no direct prompt-file links here).

```text
plan → TDD (failing test) → implement → review (Gates A–D)
    → [grounding | security as required] → verify → progress/report
```

Orchestration contract, skills, stop conditions, and report format live in `AGENTS.md` §10.1 / the active phase contract (do not fork conflicting copies).

## 1. Plan

- Task source: active `docs/plans/*-implementation-plan.md`.
- Stage entry: active-stage phases via [`AGENTS.md` §10](../../AGENTS.md#10-stage-development-supplement).
- Orchestrator owns the Task N checklist; does not write domain code when subagent-driven mode is on.
- Skill: `superpowers:writing-plans` (create plan); execution — `subagent-driven-development`.

## 2. TDD

1. Implementer writes a **failing** test (contract from the task).
2. Runs the exact command — expect FAIL (evidence).
3. Minimal implementation.
4. Same test — PASS (evidence).
5. On red after a “fix” — `superpowers:systematic-debugging`, not guesswork.

## 3. Implement

- Fresh Task subagent `generalPurpose` per task.
- Self-contained prompt: Files paths, Interfaces, Spec §§, Global Constraints, Acceptance.
- Commit only if the human asked.
- Local-only: no push / gh / remote deploy without an explicit command.

## 4. Review (Implementer ≠ Reviewer)

Separate subagent as Reviewer. Gates (details — `role-prompts/reviewer.md`):

| Gate | Check |
|------|--------|
| **A** | Spec / invariants: no dual-write, no rights-from-Kafka, ledger append-only, tenant, PaymentProviderPort, dual-id, Celery ≠ Kafka publisher |
| **B** | async ORM without lazy-load, module boundaries §9, tests, coverage |
| **C** | secrets, webhook HMAC, hashed API keys, BIGINT not in API, RBAC |
| **D** | retries / idempotency, poison / DLQ, stale cache, illegal SM, migration expand-only, pause/recon without mutating amounts |

Verdict: **APPROVE** | **REQUEST CHANGES**. On REQUEST CHANGES — fix → re-review. Self-APPROVE forbidden.

Security-review on tasks called out in the phase prompt / plan (webhooks, keys, usage ingest, dunning pause, rate limit, etc.).

## 5. Docs-grounding

For plan patterns (SQLAlchemy async, Alembic, FastAPI lifespan, outbox SKIP LOCKED, Kafka, Redis, Celery vs relay, Stripe HMAC, PG partitions, OTel, uv):

1. Grounding agent compares implementation to official docs for major versions in Spec §5.
2. **Sources consulted** field in the task report.
3. Spec↔docs conflict: product invariants from Spec; library API from docs; trade-off → ADR.

## 6. Verify

Before declaring a task / phase / stage Done — `superpowers:verification-before-completion`: fresh local commands and their output, not “should work”.

Quality gates (`spec.md` §10.4): ruff 0, mypy strict 0, unit cov ≥ 80% (services+domain), integration green via Testcontainers (`make test-integration`).

## Human checkpoints

Checkpoint canon — [`AGENTS.md` §10](../../AGENTS.md#10-stage-development-supplement) for the active stage.
Do not ask “continue?” between ordinary in-phase tasks.
Stop is mandatory at human checkpoints and on BLOCKED / security stop-the-line.

## Progress

After each Task:

- `.superpowers/sdd/progress.md` — status / review / notes (gitignored local harness);
- plan Step checklists `- [x]`.

After a phase — update `.superpowers/sdd/progress.md` with phase DoD evidence and link to `spec.md` §11.
