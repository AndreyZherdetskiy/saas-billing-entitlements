# AGENTS.md — Billing & Entitlements Platform

**Entry point** for all agent and subagent development in this repository per [`spec.md`](spec.md) **v3.2**.

Before any Task, phase, plan, review, or code change, the agent (parent and subagent) **must** rely on this file: §1–9 — standing rules; §10 — operational links for the active stage. Details and long-form text live in [`docs/`](docs/) — only invariants, navigation, and responsibilities are here; discrepancies with `docs/` are resolved by updating **this** file (see §0.3).

| Next | Path |
|------|------|
| Product / DoD / stack | [`spec.md`](spec.md) |
| Workflow | [`docs/agentic/workflow.md`](docs/agentic/workflow.md) |
| Skills | [`docs/agentic/skills-map.md`](docs/agentic/skills-map.md) |
| Subagent roles | [`docs/agentic/role-prompts/`](docs/agentic/role-prompts/) |
| Phase prompts (agents) | §10.1 |

---

## 0. Reading order and `docs/` map

### 0.1. Required reading order

1. **`AGENTS.md`** (this file) — environment, invariants, orchestration, gates, anti-patterns, stage §10.
2. **`spec.md`** — affected product §§, DoD §11, stack §5, structure §9, tests §10.
3. **`docs/adr/`** — Accepted ADRs (+ amendments) relevant to the task.
4. **`docs/plans/`** — active implementation plan (Files, Steps, Acceptance).
5. **`docs/agentic/`** — workflow, skills, role-prompts. Phase prompts per §10.1 (`PROMPT_COMMON` → `PROMPT_PHASE_*`) live in the **local SDD harness (gitignored)**.
6. As needed: **`docs/runbooks/`**, **`docs/slo.md`**, root README/CONTRIBUTING — ops, SLO, onboarding.

A subagent receives a self-contained brief; if the brief references spec/ADR/`docs/` — read those files, do not rely on parent "memory".

### 0.2. `docs/` map (what to use)

| Section | Purpose | When to read |
|---------|---------|--------------|
| [`docs/adr/`](docs/adr/) | Architecture decisions (…, partitions, **012 no sharding**, **013 Prom scoped Adopt** (LGTP profile), **014 idempotency_responses Defer** (Accepted), **015 API-key SHA-256 lookup + auth L1** (Accepted), **003 entitlement cache + snapshot L1** (Accepted), …) | Any task touching a pattern; Gate A |
| [`docs/plans/`](docs/plans/) | Stage-by-stage implementation plans | Orchestration and Implementer of active stage |
| [`docs/agentic/`](docs/agentic/) | Workflow, skills, role-prompts | Subagent dispatch |
| local SDD harness (gitignored) | Local phase / harness prompts | Phase start; not product docs |
| [`docs/runbooks/`](docs/runbooks/) | Alert response: outbox-lag, webhook-replay, **webhook-secret-rotation** (S3), recon-mismatch, **dunning-stuck** (operational S2), **dlq-replay** (operational S3), stubs entitlement-latency / ready-probe-fail; ZDT — migration-zdt-usage; **helm-kind-smoke**, **replica-lag**, **pgbouncer-pools** (S3); **load-locust** (Locust smoke additive); **local-compose-profiles** (`make perf-up` overlay) | Ops, NFR §8.5; migrations §8.9; DoD §11.2 → `.superpowers/sdd/progress.md` (gitignored local harness) |
| [`docs/perf/`](docs/perf/) | k6 profiles A–E + Locust smoke; laptop hot-path characterization [`2026-03-04-hot-path-perf.md`](docs/perf/2026-03-04-hot-path-perf.md); prod-like `make perf-up` hunt [`2026-03-07-prodlike-hunt.md`](docs/perf/2026-03-07-prodlike-hunt.md); `make load-*` / `make load-locust` / `make load-locust-otel` / `make load-k6-grafana` / `make perf-up` | Load NFR §8.1.1 (k6 DoD; Locust additive; Grafana opt-in). 2026-03-04 `load-*` overlay last hold 1000 RPS / break 1500 RPS (pool 8+4). 2026-03-07 `perf-up` overlay last hold **1500 RPS** / break **2000 RPS** (pool 2+1, relay×2, 1 replica / 4 workers); limiter SUT evaluate-path latency (CPU peg unproven on WSL `docker stats`) |
| [`docs/slo.md`](docs/slo.md) | **SLI→SLO** only (+ metrics/alerts); no separate external SLA (`spec.md` §8.5) | Observability, DoD, NFR |

Conflict priority: **product invariant** → `spec.md` + Accepted ADR; **Task / Acceptance order** → active plan; **agent operational rules** → `AGENTS.md` §2–9; **library APIs** → official docs for majors from §5 (Grounding).

### 0.3. Keeping `AGENTS.md` in sync when `docs/` changes

`AGENTS.md` must remain the current entry point. **In the same task / session** where files under `docs/` change, the agent (Docs / Orchestrator / Implementer per brief) **must** review and update the corresponding sections of this file when needed:

| Change in `docs/` | Update in `AGENTS.md` |
|-------------------|------------------------|
| New / renamed ADR, Accepted status change | §2 (invariants / ADR refs), §0.2, §10 if stage-impact |
| New or shifted plan / Tasks / checkpoints | §10 (phase tables, stops, plan paths) |
| New `PROMPT_PHASE_*` / index / `PROMPT_COMMON` | §10.1, phase tables; if contract changes — §5–6 |
| Edits to `workflow.md` / `skills-map.md` / `role-prompts/` | §4–7, header links; anti-patterns §9 |
| New runbooks / substantial index edits | §0.2, §3 (`docs/…`), §10 if needed |
| New `docs/perf/` load reports | §0.2, §10.1 if stage-impact |
| Edits to `slo.md` (metrics, alerts, thresholds) | §0.2; quality/NFR wording in §2 if diverging from spec — spec first |
| Stage 3 plan/prompts appear | §10.4 |

Do not duplicate full ADR/runbook/prompt text here — only **navigation, invariants, and operational rules**. If `docs/` changed but `AGENTS.md` was not updated — the task is **not Done** (verification: entry point is stale).

---

## 1. Environment

- Work **locally only**: Docker Compose, uv, pytest, local git.
- **FORBIDDEN** without explicit human command: `git push`, `gh pr create` / remote mutations, deploy to staging/production, publishing images to a registry.
- Commits — only when the human asks.
- CI (GitHub Actions) — lint, typecheck, unit/integration tests, plus separate load jobs (`load-harness`, `load-locust-smoke`); demo keys from `.env.example` only. k6 A–E remain local (`make load-*`).
- Subagents: Cursor built-in models only (see user/global rule); Implementer ≠ Reviewer.

## 2. Invariants (Global Constraints)

From `spec.md` (architecture §4, identifiers §6.2, stack §5, NFR §8, tests §10) and Accepted ADRs in `docs/adr/`:

1. **PostgreSQL** — source of truth for operational rights; **Kafka** — integration boundary (facts after commit) — ADR-002.
2. **Dual-write is forbidden.** Transactional outbox + separate `outbox-relay` process only (not Celery-publish of domain facts) — §12.3 / ADR-001 / ADR-004.
3. **Evaluate entitlements does not read Kafka.** Redis TTL 30–60s; snapshot key `ent:org:{id}:snapshot` (version bump **deletes** that key; version key remains for HTTP `version`); per-process snapshot L1 in front of Redis ([ADR-003](docs/adr/003-entitlement-cache.md)); auth L1 (SHA-256 digest → `AuthContext`, TTL, [ADR-015](docs/adr/015-api-key-sha256-lookup.md)); full tenant hit → no Postgres session, no Redis GET; miss → PostgreSQL. Usage write — separate path — ADR-003.
4. **Ledger append-only.** No UPDATE/DELETE by application code; reversal = new entry with `reverses_entry_id` — ADR-006.
5. **PaymentProviderPort** + mock Stripe (until live replacement per stage 3 ADR); no PAN/PCI in our DB; no live Stripe SDK in domain — ADR-005.
6. **dual-id / PK policy (§6.2 / ADR-010):** BIGINT identity + `public_id` UUIDv7 on `organizations`, `subscriptions`, `invoices`, `usage_events`, public `ledger_entries`. Catalog = UUIDv7 PK. `outbox_messages` = BIGINT PK **without** `public_id`. Sequential `id` never in DTO/API/OpenAPI.
7. **Tenant isolation:** every request filtered by `organization_id` from auth (except `platform_admin`). RBAC — `spec.md` §2.2.
8. **Celery** — batch/scheduled jobs; **idempotency on retry** required; domain facts — only via outbox in the same TX — ADR-004.
9. **Usage:** write on primary; RANGE partitions `usage_events` by month (when in scope) — ADR-011; recon / dunning pause **do not** mutate ledger/invoice amounts — ADR-007 / ADR-008.
10. **Quality gates (§10.4):** ruff 0, mypy strict 0, unit coverage ≥ 80% (services+domain), integration via Testcontainers on the Docker host (`make test-integration`). Live API smoke is `make load-locust`, not the integration job.
11. **Repo structure** — spec §9; required tests — §10.2; stage DoD — §11; ZDT migrations — ADR-009.
12. **Docs language:** all **tracked** documentation (`README.md`, `AGENTS.md`, `CONTRIBUTING.md`, `spec.md`, `docs/**`, Helm/CI comments) is professional English for operators and SRE. Code identifiers stay as-is.
13. **No sharding** in stages 1–3 scope — [ADR-012](docs/adr/012-no-sharding-stages-1-3.md) (§12.13). Read replica — per stage 3 ADR only.
14. **HTTP `idempotency_responses`** — Defer post–Stage 3 ([ADR-014](docs/adr/014-idempotency-responses-defer.md)); `Idempotency-Key` + usage/webhook domain keys required.
15. **API keys** — CSPRNG (`bp_` + `secrets.token_urlsafe(32)`); persist **only** SHA-256 hex (64 chars) in `api_keys.key_hash` with a **unique** lookup. Authenticate: `SHA-256(bearer)` then one indexed `SELECT` on miss; per-process auth L1 with TTL + invalidate on rotate/revoke ([ADR-015](docs/adr/015-api-key-sha256-lookup.md)). Prefix is display-only (non-unique). Forbidden: password KDF (bcrypt/argon2) on this path; prefix-scan + N verifies; plaintext keys.

## 3. Repository structure (target §9)

```
src/billing_platform/   # api (HTTP + colocated DTOs), domain, services, integrations, events/schemas, workers, outbox_relay
alembic/
deploy/compose|docker|helm/billing-platform/
demo_ui/
tests/unit|integration   # e2e/ — roadmap (spec §10.2); critical paths in integration
docs/adr|plans|agentic|runbooks|perf   # + docs/slo.md
```

## 4. Skills / subagents

| Moment | Skill / Task | Details |
|--------|----------------|--------|
| Plan | `superpowers:writing-plans` | [`skills-map.md`](docs/agentic/skills-map.md) |
| Execution | `superpowers:subagent-driven-development` | + [`role-prompts/orchestrator.md`](docs/agentic/role-prompts/orchestrator.md) |
| Implement Task N | Task `generalPurpose` | [`role-prompts/implementer.md`](docs/agentic/role-prompts/implementer.md) |
| Review | Reviewer ≠ Implementer | [`role-prompts/reviewer.md`](docs/agentic/role-prompts/reviewer.md) |
| Security | `security-review` / Security role | [`role-prompts/security.md`](docs/agentic/role-prompts/security.md) |
| Grounding | stack §5 library docs | [`role-prompts/grounding.md`](docs/agentic/role-prompts/grounding.md) |
| Docs / ADR / sync AGENTS | Docs role | [`role-prompts/docs.md`](docs/agentic/role-prompts/docs.md) |
| Tests / pyramid | Test role | [`role-prompts/test.md`](docs/agentic/role-prompts/test.md) |
| Bug / red test | `superpowers:systematic-debugging` | — |
| Before "done" | `superpowers:verification-before-completion` | + §0.3 if `docs/` touched |
| Worktree | `superpowers:using-git-worktrees` | on human request |

## 5. Orchestration

1. Start from **this file** → active `PROMPT_COMMON` + `PROMPT_PHASE_*` / plan (§0.1).
2. Per task — fresh Implementer with self-contained prompt (links to Files / spec / ADR from `docs/`).
3. After task — separate Reviewer (Gates A–D). Same agent must not APPROVE itself.
4. Parallel Task only when explicitly marked in plan + sync point.
5. Stop-the-line: REQUEST CHANGES / red tests / grounding failure / security BLOCK → fix → re-review.
6. Between ordinary tasks within a phase, do not ask "continue?".
7. Human checkpoint / BLOCKED — stop until explicit human command.
8. Changed `docs/` → update `AGENTS.md` (§0.3) before declaring Done.

## 6. Review Gates (every task)

| Gate | Focus |
|------|--------|
| **A** | Spec / ADR / invariants (dual-write, Kafka-rights, ledger, tenant, dual-id, Port, Celery≠Kafka) |
| **B** | quality (async ORM, modules §9, tests, mypy/ruff) |
| **C** | security (secrets, HMAC, hashed keys, RBAC, BIGINT not in API) |
| **D** | adversarial (retries, poison, stale cache, illegal SM, migration expand-only) |

## 7. Docs-grounding (libraries) and project `docs/`

- **Project `docs/`** — read per §0; do not ignore ADR/runbooks/slo in favor of "code only".
- **Official library docs** (Grounding) required for stack §5 patterns: SQLAlchemy 2 async, Alembic expand/contract, FastAPI lifespan, outbox SKIP LOCKED, Kafka at-least-once, Redis invalidation / rate limit, Celery vs relay, Stripe webhook HMAC, PG partitions, OpenTelemetry, uv.
- Spec↔library docs conflict: product invariants from spec/ADR; library APIs from current docs; trade-off → ADR amendment + update §2 / §0.2 here if needed.

## 8. Local commands

```bash
uv sync --group dev
uv run pre-commit install
docker compose -p billing-platform -f deploy/compose/docker-compose.yml up -d --build
uv run alembic upgrade head
uv run pre-commit run --all-files
make lint typecheck test-unit test-integration
# test-unit / test-integration: Docker daemon required (Testcontainers). Helm CLI needed for chart tests (CI installs it).
# CI load (separate jobs): load-harness (unit + locust --list) + load-locust-smoke (compose-core + make load-locust)
uv sync --group load && make load-locust   # Locust smoke (additive; not profile A 3k RPS DoD)
make perf-up                               # prod-like ceiling overlay (not default compose-core)
make compose-down                          # tear down all profiles + perf overlay (volumes kept)
# Optional Grafana: make observability-up && make load-locust-otel && make load-k6-grafana
```

## 9. Anti-patterns

- Start development / subagent dispatch without this file
- One agent writes and APPROVEs itself
- Placeholder "TBD" / "add tests" / "same as Task N"
- dual-write / rights-from-Kafka / mutable ledger / live Stripe SDK in domain / sharding "just in case"
- Push/PR as mandatory orchestration step
- Next-stage scope in current Tasks without roadmap-only mark
- Declare Done without fresh acceptance command run
- Leak `id BIGINT` into JSON API
- Publish domain facts from Celery bypassing outbox
- Change `docs/` (ADR, plans, prompts, runbooks, slo, agentic) **without** updating corresponding `AGENTS.md` sections (§0.3)

---

## 10. Stage development (supplement)

Below — operational links to plans and phase prompts. **Do not weaken** §0–9. When plans/prompts change — update this section (§0.3).

### 10.1. Common entry

| Artifact | Path |
|----------|------|
| Workflow | [`docs/agentic/workflow.md`](docs/agentic/workflow.md) |
| Role prompts | [`docs/agentic/role-prompts/`](docs/agentic/role-prompts/) |
| Phase prompts (local) | local SDD harness (gitignored) |
| Skills map | [`docs/agentic/skills-map.md`](docs/agentic/skills-map.md) |
| Progress / DoD evidence | `.superpowers/sdd/progress.md` (gitignored local harness) |
| ADR | [`docs/adr/`](docs/adr/) |
| Runbooks | [`docs/runbooks/`](docs/runbooks/) (index + 4 DoD + 2 stub) |
| SLI/SLO | [`docs/slo.md`](docs/slo.md) (no separate SLA) |
| Load / perf (§8.1.1) | [`docs/perf/`](docs/perf/) — k6 A–E + Locust smoke ([`locust-smoke-report.md`](docs/perf/locust-smoke-report.md)); laptop hot-path [`2026-03-04-hot-path-perf.md`](docs/perf/2026-03-04-hot-path-perf.md) (`load-*` overlay last hold 1000 RPS / break 1500 RPS, pool 8+4); prod-like hunt [`2026-03-07-prodlike-hunt.md`](docs/perf/2026-03-07-prodlike-hunt.md) (`perf-up` last hold **1500 RPS** / break **2000 RPS**, pool 2+1, relay×2); `make load-*` / `make load-locust` / `make load-locust-otel` / `make load-k6-grafana` / `make perf-up` ([runbook](docs/runbooks/load-locust.md), [compose profiles](docs/runbooks/local-compose-profiles.md), [observability](deploy/observability/README.md)); CI gates: `load-harness` + `load-locust-smoke` in [`.github/workflows/ci.yml`](.github/workflows/ci.yml); Locust additive, k6 remains DoD; `make perf-up` overlay is ceiling characterization, not profile A DoD |

Read [`docs/agentic/workflow.md`](docs/agentic/workflow.md) and the active stage plan before any phase work.

### 10.2. Stage 1 — Foundation (`spec.md` §3.3 / §11.1)

**Plan:** [`docs/plans/2026-02-14-stage1-implementation-plan.md`](docs/plans/2026-02-14-stage1-implementation-plan.md)

| Phase | Tasks | Prompt |
|-------|-------|--------|
| P Plan | — | `PROMPT_CREATE_STAGE1_IMPLEMENTATION_PLAN.md` |
| 0 Bootstrap | 0 | `PROMPT_PHASE_0_BOOTSTRAP.md` (checkpoint #1) |
| 1 Compose + schema | 1–2 | `PROMPT_PHASE_1_COMPOSE_SCHEMA.md` |
| 2 Tenant / catalog / subs | 3–5 | `PROMPT_PHASE_2_TENANT_CATALOG_SUBS.md` |
| 3 Payments / outbox | 6–8 | `PROMPT_PHASE_3_PAYMENTS_OUTBOX.md` |
| 4 Entitlements / ledger | 9–10 | `PROMPT_PHASE_4_ENTITLEMENTS_LEDGER.md` (checkpoint #2) |
| 5 Recon / ops / demo | 11–15 | `PROMPT_PHASE_5_RECON_OPS_DEMO.md` (checkpoint #3 / §11.1) |

Stage 1 human checkpoints: after Task 0; after Task 10 / PHASE_4; before Stage 1 Done (after Task 15 / PHASE_5).

### 10.3. Stage 2 — Usage, reconciliation, dunning (`spec.md` §3.4 / §11.2)

**Status:** **Stage2 Done** (CP-S2-B accepted). Evidence: `.superpowers/sdd/progress.md` (gitignored) + `spec.md` §11.2.

**Plan:** [`docs/plans/2026-02-27-stage2-implementation-plan.md`](docs/plans/2026-02-27-stage2-implementation-plan.md)

| Phase | Tasks | Prompt | Stop |
|-------|-------|--------|------|
| P2 Plan | — | `PROMPT_CREATE_STAGE2_IMPLEMENTATION_PLAN.md` | plan accepted |
| 6 Usage partitions | 16–18 | `PROMPT_PHASE_6_USAGE_PARTITIONS.md` | end of phase |
| 7 Invoicing / period close | 19–21 | `PROMPT_PHASE_7_INVOICING_PERIOD_CLOSE.md` | **CP-S2-0** |
| 8 Grace + dunning | 22–24 | `PROMPT_PHASE_8_GRACE_DUNNING.md` | **CP-S2-A** |
| 9 Recon cron / alerts | 25–26 | `PROMPT_PHASE_9_RECON_CRON_ALERTS.md` | end of phase |
| 10 Plan change / rate limit | 27–28 | `PROMPT_PHASE_10_PLAN_CHANGE_RATE_LIMIT.md` | end of phase |
| 11 Celery / UI / DoD | 29–33 | `PROMPT_PHASE_11_CELERY_UI_DOD.md` | **CP-S2-B** / §11.2 |

Stage 2 deltas (supplement §2, do not override): RANGE `usage_events`; `DUNNING_ENABLED`; grace clock; daily recon without auto-fix; rate limit 429; Kafbat UI in Compose; Forbidden: read replica / Helm HA / sharding / live Stripe / ESP as mandatory scope.

### 10.4. Stage 3 — Scale (`spec.md` §3.5 / §11.3)

**Status:** **Stage3 Done** (CP-S3-FINAL accepted). Tasks 48–50 APPROVE; load A/C evidence PARTIAL (smoke on laptop; full RPS — stand ≥3 API). Profiles A–E: `docs/perf/` + `make load-*`.

**Plan:** [`docs/plans/2026-02-27-stage3-implementation-plan.md`](docs/plans/2026-02-27-stage3-implementation-plan.md)

| Phase | Tasks | Prompt | Stop |
|-------|-------|--------|------|
| P3 Plan | — | `PROMPT_CREATE_STAGE3_IMPLEMENTATION_PLAN.md` | plan accepted |
| 12 Helm + probes | 34–35 | `PROMPT_PHASE_12_HELM_PROBES.md` | **CP-S3-0** |
| 13 Replica + pools | 36–38 | `PROMPT_PHASE_13_READ_REPLICA_POOLS.md` | **CP-S3-A** |
| 14 Relay HA + DLQ | 39–40 | `PROMPT_PHASE_14_RELAY_HA_DLQ.md` | end of phase |
| 15 Security rotation | 41–43 | `PROMPT_PHASE_15_SECURITY_ROTATION.md` | end of phase |
| 16 Advanced entitlements | 44 | `PROMPT_PHASE_16_ADVANCED_ENTITLEMENTS.md` | end of phase |
| 17 ADR obs + partitions | 45–47 | `PROMPT_PHASE_17_OBS_SHARDING_PARTITIONS.md` | **CP-S3-B** |
| 18 Load + DoD | 48–50 | `PROMPT_PHASE_18_LOAD_DOD.md` | **CP-S3-FINAL** / §11.3 |

ADR: [`012-no-sharding`](docs/adr/012-no-sharding-stages-1-3.md) (Accepted), [`013-prometheus-grafana`](docs/adr/013-prometheus-grafana.md) (**scoped Adopt** — LGTP profile `observability`), [`014-idempotency-responses-defer`](docs/adr/014-idempotency-responses-defer.md) (**Defer**, Accepted), [`015-api-key-sha256-lookup`](docs/adr/015-api-key-sha256-lookup.md) (Accepted; SHA-256 unique lookup, not a password KDF). Roadmap-only: customer portal; **no** sharding; LGTP stack opt-in via `deploy/observability/` (default compose without Grafana); **no** `idempotency_responses` / `tests/e2e/` until amendment.

```text
Stage 3 plan accepted. Execute Phase 12 (Tasks 34–35) per the stage 3 plan
(+ AGENTS.md + ADR-012). Locally, no push; commits only on my command. Implementer ≠ Reviewer.
```

### 10.5. Ad-hoc — stack versions + full audit

Local gitignored harness (not Stage N; no Stage Done; no commit unless asked):

- `PROMPT_STACK_VERSION_UPGRADE.md` — current stable FastAPI/Celery/Redis/OTel + **Kafka 4.x** with aiokafka; then Phase A of full audit
- `PROMPT_FULL_PROJECT_AUDIT_AND_DOCS.md` — live seed audit + EN operator docs + independent RU handbook

Always attach `PROMPT_COMMON.md`. Subagents: `composer-2.5` or `cursor-grok-4.5-high`; Implementer ≠ Reviewer.

### 10.6. Ad-hoc — evaluate hit-path (2026-02-26)

Not a Stage N. No Stage Done. Plan: [`docs/plans/2026-02-26-evaluate-hit-path.md`](docs/plans/2026-02-26-evaluate-hit-path.md) (L1 + auth cache). Prior perf plan: [`docs/plans/2026-03-04-evaluate-hot-path-perf.md`](docs/plans/2026-03-04-evaluate-hot-path-perf.md). Canon: [ADR-003](docs/adr/003-entitlement-cache.md) (snapshot L1 + tenant hot path), [ADR-015](docs/adr/015-api-key-sha256-lookup.md) (SHA-256 + auth L1). Laptop overlay [`docs/perf/2026-03-04-hot-path-perf.md`](docs/perf/2026-03-04-hot-path-perf.md): last hold **1000 RPS** evaluate, break **1500 RPS**, 1 replica / 4 workers (auth L1 + snapshot L1). Pre-L1 SHA-256 on the same knobs: last hold 400 RPS, break 500 RPS.

### 10.7. Ad-hoc — prod-like perf hunt (2026-03-07)

Not a Stage N. No Stage Done. Plan: [`docs/plans/2026-03-08-prodlike-perf.md`](docs/plans/2026-03-08-prodlike-perf.md). Evidence: [`docs/perf/2026-03-07-prodlike-hunt.md`](docs/perf/2026-03-07-prodlike-hunt.md) (`make perf-up`: pool 2+1, relay×2, 1 replica / 4 workers). Last hold **1500 RPS** evaluate / break **2000 RPS**; limiter **SUT evaluate-path latency** (CPU peg unproven on WSL `docker stats`). Task 3: no `src/` change. Task 4: facts-only docs from Task 2 numbers (no remeasure after docs-only Task 3). Stand **3,000** RPS profile A DoD unchanged.
