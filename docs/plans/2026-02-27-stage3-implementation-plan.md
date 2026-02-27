# Implementation plan: Billing and entitlements platform (stage 3)

> **For agentic workers:** REQUIRED SUB-SKILL: `superpowers:subagent-driven-development`.
> Implementer ≠ Reviewer. Local: no push. Checklists `- [ ]`.
> Execute phases per [`AGENTS.md` §10.4](../../AGENTS.md#104-stage-3--scale-specmd-§35--§113) (+ Stage 3 plan; phase prompts in §10.1).

**Goal:** Close `spec.md` §3.5 / §11.3: Helm (kind/minikube) + probes/HPA → read replica + PgBouncer → relay HA + DLQ replay → key/webhook secret rotation + stronger rate limit → advanced entitlements → ADR no sharding + ADR-013 scoped Adopt (LGTP profile) → load profiles A/C + `docs/perf/` + Stage3 Done.

**Architecture:** Preserve S1+S2 invariants. Primary is the sole writer. Read replica — evaluate (cache miss) / usage reports with acceptable eventual consistency + lag fallback. Outbox-relay remains the sole Kafka domain publisher (ADR-004); ≥2 replicas without double publish. Celery — batch/cron only. Sharding is **not** introduced (ADR-012).

**Tech Stack:** as S1+S2 + Helm 3, kind/minikube, PostgreSQL streaming replica, PgBouncer, k6 (or Locust), optional Prometheus/Grafana after ADR-013 Adopt.

## Global Constraints

### From stages 1–2 (do not weaken)
- PostgreSQL SoT; Kafka post-commit bus; no dual-write; outbox + separate relay (not Celery-publish of domain facts).
- Evaluate does not read Kafka; Redis TTL + version bump; usage write is a separate path.
- Ledger append-only; dual-id; tenant filter; mock Stripe + Port; no PAN.
- Celery idempotent retries; RANGE `usage_events`; recon/dunning pause do not mutate amounts.
- Quality gates §10.4; Docs EN; ids EN; local-only; commits on request.

### Stage 3 additions
- `DATABASE_URL` (RW primary) + `DATABASE_READ_URL` (RO replica); mutations on primary only.
- Evaluate/reports: RO when `replica_lag_seconds` < threshold; else fallback primary.
- ≥2 outbox-relay replicas without double publish (`idempotency_key` / `SKIP LOCKED`).
- Helm + probes; HPA stub; API key rotation without downtime; webhook secret overlap (2 secrets).
- ADR-012 no sharding; ADR-013 **scoped Adopt** (LGTP profile `observability`); load A/C → `docs/perf/`.
- **Forbidden:** sharding implementation; 100k+ RPS DoD; writes on replica; customer portal as mandatory scope; live Stripe without ADR.

### Design locks
1. **Replica lag:** metric/check `replica_lag_seconds`; threshold in Settings; evaluate miss → RO only if lag OK.
2. **Relay HA:** all replicas poll with `FOR UPDATE SKIP LOCKED`; publish idempotent on outbox `idempotency_key` / Kafka key = outbox id.
3. **Prom:** Task 46 accepts **Adopt** or **Defer** in ADR-013 with rationale; dashboard code only on Adopt.
4. **Load:** profiles A and C (§8.1.1) **after** Helm+replica green; report required; laptop = smoke only.
5. **Advanced entitlements:** boolean / quota / rate_limit / seat in `feature_type` + evaluate enforcement (§3.5) — Task 44.
6. **Customer portal / OAuth2 / live Stripe** — roadmap only (not §11.3).

---

## A. Spec → stage 3 epics

| Epic | Spec | DoD §11.3 anchor | Tasks |
|------|------|-----------------|-------|
| Helm charts + kind | §3.5, §9 | Helm api+worker+relay + probes | 34–35 |
| HPA stub | §11.3 | HPA stub | 35 |
| Read replica + RO path | §3.5, §8.1 | evaluate/reports RO | 36–37 |
| PgBouncer / pools | §3.5 | pools | 38 |
| Relay HA (2 replicas) | §11.3, ADR-004 | 2 replicas no double publish | 39 |
| DLQ replay | §11.3 | DLQ script | 40 |
| API key rotation ZDT | §11.3, §8 | rotation without downtime | 41 |
| Webhook secret overlap | §11.3, §8 | dual-secret rotation | 42 |
| Stronger rate limit + invalid HMAC reject | §11.3 | 429 + evidence | 43 |
| Advanced entitlements | §3.5 | boolean/quota/rate/seat | 44 |
| ADR no sharding | §12.13, §11.3 | ADR in docs/adr | 45 |
| Prom Adopt\|Defer | §8.5.1, §11.3 | ADR + optional dashboards | 46 |
| Partition auto next-month | §11.3 | automation evidenced | 47 |
| Load A + C + docs/perf | §8.1.1, §10.5, §11.3 | reports | 48–49 |
| Stage3 DoD verification | §11.3 | matrix + CP-S3-FINAL | 50 |

---

## B. Global Constraints

See block above. Gate A always checks S1+S2+S3 invariants (especially: no write on replica; no Celery→Kafka; no sharding).

---

## C. Dependencies from stages 1–2

Plan **consumes** (does not rewrite):
- outbox-relay + SKIP LOCKED; Celery Beat schedule (Task 29);
- entitlements evaluate + Redis version; rate limit 429 (Task 28);
- `usage_events` partitions + `usage.create_partition` (Tasks 16/29);
- Helm **new** on top of Compose Dockerfiles; Kafbat stays Compose-demo;
- `docs/slo.md` + alert stubs (Task 26); phase-s2-dod evidence.

---

## D. Stage 3 ADR queue

1. **ADR-012** — no sharding at stages 1–3 + transition criteria (§12.13) — **Accepted**; Task 45 verify+link done.
2. **ADR-013** — scoped Adopt LGTP profile (§8.5.1) — implemented in Task 46 (amended 2026-03-02).
3. **ADR-003 amendment** — evaluate RO DSN + lag fallback — before Task 37.
4. **ADR-004 amendment** — multi-replica relay (SKIP LOCKED / no double publish) — before Task 39.
5. **ADR-009** — key/webhook secret rotation expand patterns — Tasks 41–42.
6. **ADR-011** — confirm auto next-month partition (Task 47).

---

## E. Stage 3 tasks (Tasks 34–50)

### Task 34: Helm chart scaffold (api, worker, beat, relay)

**Stage:** 3 · **Track:** infra
**Cursor:** Implementer=generalPurpose; Reviewer=generalPurpose(review); Grounding=Helm 3
**Depends on:** Stage2 Done; Dockerfiles exist
**Files:**
- Create: `deploy/helm/billing-platform/` (Chart.yaml, values, templates for api/worker/beat/outbox-relay)
- Modify: `.env.example` / values placeholders (no secrets)
- Test: `tests/integration/test_helm_template.py` **or** `helm template` checklist in task-report
**Spec:** §3.5 K8s, §11.3 Helm

**Goal:** Render Helm chart with Deployments for api, Celery worker, Celery beat, outbox-relay; ConfigMap/Secret refs; without remote registry push.

**Interfaces:**

```yaml
# values.yaml (illustrative)
replicaCount:
  api: 2
  relay: 1  # bumped in Task 39
image:
  repository: local/billing-platform
```

**Steps:**

- [x] **Failing test:** `helm template` / pytest asserts Deployments for api+worker+beat+relay exist.
- [x] **Docs-grounding:** Helm 3 charts / values.
- [x] **Implement** chart (local images / kind load).
- [x] **PASS.**
- [x] **Review Gates A–D.**
- [ ] **Commit** (on request): `feat: helm chart scaffold for stage3`

**Acceptance:** `helm template` succeeds; 4 workloads present.
**Risks:** secrets in values.

---

### Task 35: Probes + HPA stub + kind/minikube smoke

**Stage:** 3 · **Track:** infra
**Cursor:** Implementer=generalPurpose; Reviewer=generalPurpose(review)
**Depends on:** Task 34
**Files:**
- Modify: Helm templates (liveness/readiness), `deploy/helm/.../hpa.yaml` stub
- Docs: `docs/runbooks/helm-kind-smoke.md` (short)
- Test: checklist kind apply **or** `tests/integration/test_helm_probes_render.py`
**Spec:** §8.6, §11.3

**Goal:** Probes on api/relay/worker; HPA stub (min/max); documented smoke on kind/minikube.

**Steps:**

- [x] **Failing test:** templates lack readiness on api.
- [x] **Implement** probes + HPA stub + runbook.
- [x] **PASS** render; optional kind apply evidence in report.
- [x] **Review.**
- [ ] **Commit** (on request): `feat: helm probes and hpa stub`

**Acceptance:** §11.3 Helm+HPA bullets partially; full green with Task 34.
**Checkpoint hint:** **CP-S3-0** after Tasks 34–35 (Helm green).

---

### Task 36: Read replica topology (Compose + Helm values)

**Stage:** 3 · **Track:** infra|db
**Cursor:** Implementer=generalPurpose; Reviewer=generalPurpose(review); Grounding=PG replica
**Depends on:** Task 34
**Files:**
- Modify: `deploy/compose/docker-compose.yml` (postgres-replica optional profile), Helm values
- Create: `docs/runbooks/replica-lag.md`
- Config: `DATABASE_READ_URL`, `REPLICA_LAG_THRESHOLD_SECONDS`
- Test: `tests/integration/test_read_replica_dsn_config.py`
**Spec:** §3.5, §8.1 routing

**Goal:** RO DSN available in config; replica service in Compose profile / Helm; runbook lag.

**Steps:**

- [x] **Failing test:** settings lack `database_read_url`.
- [x] **Implement** config + compose/helm + runbook.
- [x] **PASS.**
- [x] **Review Gate A:** writes still use primary only.
- [ ] **Commit** (on request): `feat: read replica dsn and compose profile`

**Acceptance:** RO URL configurable; docs present.

---

### Task 37: Evaluate / usage reports on RO with lag fallback

**Stage:** 3 · **Track:** domain|api
**Cursor:** Implementer=generalPurpose; Reviewer=generalPurpose(review); Grounding=SQLAlchemy engines
**Depends on:** Task 36; ADR-003 amendment
**Files:**
- Create/Modify: `db/session.py` dual engines; `services/entitlements.py` RO path; usage report reads
- Test: `tests/unit/test_replica_lag_fallback.py`, `tests/integration/test_evaluate_uses_ro_when_fresh.py`
**Spec:** §8.1, §11.3

**Goal:** Cache miss evaluate and usage reports read RO if lag OK; else primary. Mutations untouched.

**Interfaces:**

```python
async def get_read_session(*, allow_stale: bool = False) -> AsyncSession: ...
def should_use_replica(*, lag_seconds: float | None, threshold: float) -> bool: ...
```

**Steps:**

- [x] **Failing test:** high lag → primary; low lag → RO session factory called.
- [x] **Implement** + ADR-003 amendment.
- [x] **PASS.**
- [x] **Review Gate A/D:** no write on RO engine.
- [ ] **Commit** (on request): `feat: evaluate and reports read from replica`

**Acceptance:** §11.3 read replica bullet.
**Checkpoint hint:** **CP-S3-A** after Tasks 36–38.

---

### Task 38: PgBouncer + pool sizing

**Stage:** 3 · **Track:** infra
**Cursor:** Implementer=generalPurpose; Reviewer=generalPurpose(review); Grounding=PgBouncer
**Depends on:** Task 36
**Files:**
- Modify: compose/helm (pgbouncer service), app pool settings
- Test: `tests/integration/test_pgbouncer_compose.py` **or** config assert + docs
**Spec:** §3.5

**Goal:** PgBouncer before primary (and optional replica); documented pool sizes; app connects via bouncer in S3 profile.

**Steps:**

- [x] **Failing check:** no pgbouncer service.
- [x] **Implement.**
- [x] **PASS.**
- [x] **Review.**
- [ ] **Commit** (on request): `feat: pgbouncer pooling for stage3`

**Acceptance:** pool path documented and testable locally.

---

### Task 39: Outbox-relay dual replica without double publish

**Stage:** 3 · **Track:** workers|infra
**Cursor:** Implementer=generalPurpose; Reviewer=generalPurpose(review); Security=optional; Grounding=SKIP LOCKED
**Depends on:** Task 34; ADR-004 amendment
**Files:**
- Modify: `outbox_relay/`, Helm `replicaCount.relay: 2`
- Test: `tests/integration/test_relay_two_replicas_no_dup.py`
**Spec:** §11.3, ADR-004

**Goal:** Two relay replicas must not publish the same outbox message twice (idempotency + SKIP LOCKED).

**Interfaces:**

```python
async def claim_outbox_batch(session, *, limit: int) -> list[OutboxMessage]:
    # SELECT ... FOR UPDATE SKIP LOCKED
```

**Steps:**

- [x] **Failing test:** two workers claiming same rows → duplicate Kafka (simulate).
- [x] **Implement** claim semantics + Helm replicas=2.
- [x] **PASS** no duplicate publish.
- [x] **Review Gate A.**
- [ ] **Commit** (on request): `feat: multi-replica outbox relay without duplicates`

**Acceptance:** §11.3 two relay replicas without double publish.

---

### Task 40: DLQ replay script

**Stage:** 3 · **Track:** ops
**Cursor:** Implementer=generalPurpose; Reviewer=generalPurpose(review)
**Depends on:** outbox DLQ table (S1)
**Files:**
- Create: `scripts/replay_outbox_dlq.py`, `docs/runbooks/dlq-replay.md`
- Test: `tests/integration/test_dlq_replay_script.py`
**Spec:** §11.3

**Goal:** Idempotent replay of dead-letter outbox rows → back to publishable state / re-enqueue; audit log.

**Steps:**

- [x] **Failing test:** DLQ row not replayed.
- [x] **Implement** script + runbook.
- [x] **PASS.**
- [x] **Review Gate D:** no ledger mutation.
- [ ] **Commit** (on request): `feat: outbox dlq replay script`

**Acceptance:** §11.3 DLQ bullet.

---

### Task 41: API key rotation without downtime

**Stage:** 3 · **Track:** api|security
**Cursor:** Implementer=generalPurpose; Reviewer=generalPurpose(review); Security=security-review
**Depends on:** Task 3 auth
**Files:**
- Modify: `services/api_keys.py`, `api/v1/admin/...`
- Test: `tests/integration/test_api_key_rotation.py`
**Spec:** §11.3, §8

**Goal:** Create new key, both valid in overlap window, revoke old; auth without downtime.

**Interfaces:**

```python
async def rotate_api_key(session, *, organization_id: int, actor_key_id: UUID) -> tuple[ApiKey, str]:
    # returns new key row + raw secret once
```

**Steps:**

- [x] **Failing test:** after rotate, old+new both authenticate until revoke.
- [x] **Implement.**
- [x] **PASS** + Security.
- [ ] **Commit** (on request): `feat: api key rotation without downtime`

**Acceptance:** §11.3 key rotation.

---

### Task 42: Webhook secret rotation with overlap

**Stage:** 3 · **Track:** api|security
**Cursor:** Implementer=generalPurpose; Reviewer=generalPurpose(review); Security=security-review
**Depends on:** webhook HMAC (S1)
**Files:**
- Modify: settings (`MOCK_STRIPE_WEBHOOK_SECRET`, `MOCK_STRIPE_WEBHOOK_SECRET_PREVIOUS`), webhook verify
- Test: `tests/unit/test_webhook_secret_overlap.py`
**Spec:** §11.3, §8

**Goal:** Verify accepts current **or** previous secret; document rotation runbook.

**Steps:**

- [x] **Failing test:** previous secret rejected.
- [x] **Implement** dual-secret verify + runbook.
- [x] **PASS** + Security.
- [ ] **Commit** (on request): `feat: webhook secret overlap rotation`

**Acceptance:** §11.3 webhook secret rotation.

---

### Task 43: Stronger rate limiting + invalid signature reject evidence

**Stage:** 3 · **Track:** api|security
**Cursor:** Implementer=generalPurpose; Reviewer=generalPurpose(review); Security=security-review
**Depends on:** Task 28
**Files:**
- Modify: rate limit defaults/tiers; webhook 401/403 on bad HMAC tests if missing
- Test: `tests/integration/test_rate_limit_stricter.py`, assert invalid signature rejected
**Spec:** §11.3

**Goal:** Stronger limit (config tier / lower default for non-admin) + explicit test rejecting webhook with invalid signature (§11.3).

**Steps:**

- [x] **Failing test:** stricter limit; bad HMAC accepted (must fail).
- [x] **Implement.**
- [x] **PASS** + Security.
- [ ] **Commit** (on request): `feat: stronger rate limits and webhook signature reject`

**Acceptance:** §11.3 rate + invalid signature bullets.

---

### Task 44: Advanced entitlements — boolean, quota, rate, seat

**Stage:** 3 · **Track:** domain
**Cursor:** Implementer=generalPurpose; Reviewer=generalPurpose(review)
**Depends on:** entitlements S1/S2
**Files:**
- Modify: `services/entitlements.py`, feature_type handling, `api/v1/entitlements.py` request/response models
- Test: `tests/unit/test_feature_types_evaluate.py`, integration evaluate matrix
**Spec:** §3.5 advanced entitlements, §6 features

**Goal:** Evaluate correctly handles `boolean | quota | rate_limit | seat` (deny/allow/limit semantics); seats from subscription items quantity where applicable.

**Interfaces:**

```python
def decide_feature(*, feature_type: str, limit: int | None, used: int, seats: int | None) -> Decision: ...
```

**Steps:**

- [x] **Failing test:** seat/rate_limit types mishandled.
- [x] **Implement.**
- [x] **PASS.**
- [x] **Review.**
- [ ] **Commit** (on request): `feat: advanced entitlement feature types`

**Acceptance:** §3.5 advanced rights; OpenAPI examples updated.

---

### Task 45: ADR-012 «no sharding» (Accepted)

**Stage:** 3 · **Track:** docs
**Cursor:** Implementer=generalPurpose; Reviewer=generalPurpose(review)
**Depends on:** — (can parallel early)
**Files:**
- Ensure: `docs/adr/012-no-sharding-stages-1-3.md` (created in planning; Task verifies links from AGENTS/README)
- Test: n/a — docs review
**Spec:** §12.13, §11.3

**Goal:** ADR Accepted in repo; links from AGENTS/README; transition criteria recorded.

**Steps:**

- [x] **Verify** ADR-012 present and linked.
- [x] **Update** nav if needed (AGENTS §0.3).
- [x] **Review.**
- [ ] **Commit** (on request): `docs: adr-012 no sharding stages 1-3`

**Acceptance:** §11.3 ADR no sharding.

---

### Task 46: ADR-013 Prometheus/Grafana scoped Adopt (+ LGTP wiring)

**Stage:** 3 · **Track:** docs|ops
**Cursor:** Implementer=generalPurpose; Reviewer=generalPurpose(review); Grounding=Prom/Grafana/OTel
**Depends on:** Task 26 slo stubs
**Files:**
- Create/Update: `docs/adr/013-prometheus-grafana.md`, `deploy/observability/`, compose profile `observability`
- Test: docs checklist; optional `make observability-up` smoke
**Spec:** §8.5.1, §11.3

**Goal:** **Scoped Adopt** — opt-in LGTP profile; default compose without observability backends.

**Steps:**

- [x] **Brainstorming** Adopt vs Defer (briefly in ADR).
- [x] **Write** ADR-013 decision (initial **Defer**; **amended 2026-03-02** → scoped Adopt).
- [x] **Implement** LGTP profile `observability` + docs (`deploy/observability/README.md`, `docs/slo.md`).
- [x] **Review.**
- [ ] **Commit** (on request): `docs: adr-013 scoped adopt lgtp profile`
**Checkpoint hint:** **CP-S3-B** after Task 46.

**Acceptance:** §11.3 Prom bullet; no false "Prometheus always on" in user-facing docs.

---

### Task 47: usage_events next-month partition automation evidence

**Stage:** 3 · **Track:** workers|qa
**Cursor:** Implementer=generalPurpose; Reviewer=generalPurpose(review)
**Depends on:** Tasks 16, 29; ADR-011
**Files:**
- Verify/Modify: `usage.create_partition` beat + ensure next month
- Test: `tests/integration/test_partition_next_month_job.py`
**Spec:** §11.3

**Goal:** Prove auto-creation of partition for **next** month (not only current); close S2 gap if any.

**Steps:**

- [x] **Failing test:** next month partition missing after job.
- [x] **Implement** gap fix if needed.
- [x] **PASS.**
- [x] **Review.**
- [ ] **Commit** (on request): `feat: ensure next-month usage partition automation`

**Acceptance:** §11.3 partition automation.

---

### Task 48: Load profile A — evaluate peak (k6)

**Stage:** 3 · **Track:** perf
**Cursor:** Implementer=generalPurpose; Reviewer=generalPurpose(review); Grounding=k6
**Depends on:** Tasks 34–38 (Helm+replica preferred); §10.5
**Files:**
- Create: `docs/perf/k6_evaluate_peak.js`, `docs/perf/profile-a-report.md`
- Test: smoke k6 locally; full report on capable stand
**Spec:** §8.1.1 profile A, §11.3

**Goal:** Scenario A: **3,000** RPS evaluate / 10 min; p99 < 50 ms with ≥3 API replicas; error rate < 0.1%; report in `docs/perf/`.

**Steps:**

- [x] **Failing check:** no k6 script.
- [x] **Implement** script + run on stand; document results (or blocked with honest PARTIAL + hardware note — but DoD requires report).
- [x] **PASS** criteria or document blocker for human.
- [x] **Review.**
- [ ] **Commit** (on request): `perf: profile a evaluate peak report`

**Acceptance:** profile A report exists meeting §8.1.1 (or explicit human waiver — default no waiver).

---

### Task 49: Load profile C — mixed (k6)

**Stage:** 3 · **Track:** perf
**Cursor:** Implementer=generalPurpose; Reviewer=generalPurpose(review)
**Depends on:** Task 48
**Files:**
- Create: `docs/perf/k6_mixed.js`, `docs/perf/profile-c-report.md`
**Spec:** §8.1.1 profile C, §11.3

**Goal:** Mixed **5,000** HTTP RPS (evaluate **3,000** + usage **1,500** + admin **500**; band **4,500–6,000**) / 10 min; outbox_lag p99 < 30s under peak; report.

**Steps:**

- [x] **Implement** + report.
- [x] **PASS.** (PARTIAL — smoke only; full C on capable stand pending)
- [x] **Review.**
- [ ] **Commit** (on request): `perf: profile c mixed report`

**Acceptance:** §11.3 load bullet complete with A+C.

---

### Task 50: Stage 3 DoD verification + CP-S3-FINAL

**Stage:** 3 · **Track:** docs|qa
**Cursor:** Implementer=generalPurpose; Reviewer=generalPurpose(review); Skills=`verification-before-completion`
**Depends on:** Tasks 34–49
**Files:**
- Create: `docs/progress.md` (stage 3 DoD evidence)
- Modify: README Stage 3 section; AGENTS §10.4 table filled
**Spec:** §11.3

**Goal:** Evidence matrix for each §11.3 checkbox; human checkpoint Stage3 Done.

**Steps:**

- [x] **Write** matrix with fresh commands.
- [x] **Review Gates A–D.**
- [ ] **Human checkpoint** — do not declare Stage3 Done without “accept”.
- [ ] **Commit** (on request): `docs: stage3 dod evidence`

**Acceptance:** §11.3 all PASS with evidence (scoped Adopt LGTP opt-in; default compose without Prom).

---

## F. Roadmap after stage 3 (not Tasks)

- Customer-facing billing portal (optional)
- OAuth2 client credentials
- Live Stripe ADR
- Full Prom stack if Defer → revisit
- Sharding **only** per ADR-012 criteria
- Profile B usage ingest load (optional beyond §11.3 A+C)

---

## G. Test strategy

| Level | Command | Threshold |
|---------|---------|-------|
| Lint/types/unit | `make lint typecheck test-unit` | S1+S2 + new |
| Integration | `make test-integration` | compose.test + replica profile |
| Helm | `helm template` / kind smoke | Tasks 34–35 |
| Perf | k6 A/C | Tasks 48–49 → `docs/perf/` |
| S3 acceptance | Task 50 matrix | 100% §11.3 |

---

## H. Risks → runbook → Task

| Failure | Runbook | Task |
|---------|---------|------|
| Replica lag / stale evaluate | `replica-lag.md` | 36–37 |
| Double Kafka publish | outbox-lag / relay HA notes | 39 |
| DLQ poison | `dlq-replay.md` | 40 |
| Key/secret rotation mistake | webhook-replay + new rotation notes | 41–42 |
| Load stand insufficient | `docs/perf/` honesty | 48–49 |
| Helm probe fail | `helm-kind-smoke.md`, `ready-probe-fail.md` | 35 |

---

## I. Calendar 6–8 weeks + checkpoints

| Weeks | Focus | Tasks | Phase |
|--------|--------|-------|------|
| 1–2 | Helm + probes + HPA | 34–35 | PHASE_12 · **CP-S3-0** |
| 2–3 | Replica + PgBouncer | 36–38 | PHASE_13 · **CP-S3-A** |
| 3–4 | Relay HA + DLQ | 39–40 | PHASE_14 |
| 4–5 | Security rotation + rate | 41–43 | PHASE_15 |
| 5 | Advanced entitlements | 44 | PHASE_16 |
| 5–6 | ADR sharding + Prom + partitions | 45–47 | PHASE_17 · **CP-S3-B** |
| 6–8 | Load A/C + DoD | 48–50 | PHASE_18 · **CP-S3-FINAL / §11.3** |

**Stage 3 human checkpoints:**
- **CP-S3-0:** after Tasks 34–35 (Helm green).
- **CP-S3-A:** after Tasks 36–38 (replica + evaluate RO).
- **CP-S3-B:** after Task 46 (Prom Adopt|Defer).
- **CP-S3-FINAL (#5):** after Task 50 / §11.3 — Stage3 Done only with “accept”.

---

## J. DoD = `spec.md` §11.3

Copy §11.3 checklist into Task 50 matrix; each item → evidence path.

---

## Sources consulted (planning)

- `spec.md` §3.5, §8.1 / §8.1.1, §8.5.1, §10.5, §11.3, §12.13
- Plans S1/S2; `phase-s2-dod.md`
- ADR-002, 003, 004, 009, 011
- Helm / PG replica / k6 / Prom docs — grounding at Task execution (WebFetch)
