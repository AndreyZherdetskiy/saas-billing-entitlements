# Implementation plan: Billing and entitlements platform (stage 2)

> **For agentic workers:** REQUIRED SUB-SKILL: `superpowers:subagent-driven-development`.
> Implementer ≠ Reviewer. Local: no push. Checklists `- [ ]`.
> Execute phases per [`AGENTS.md` §10.3](../../AGENTS.md#103-stage-2--usage-reconciliation-dunning-specmd-§34--§112) (+ Stage 2 plan; phase prompts in §10.1).

**Goal:** Close `spec.md` §3.4 / §11.2: usage ingest + partitions + aggregates → period close / invoices / ledger `usage_charge` → grace + dunning → daily recon cron → plan change + rate limit 429 → Celery idempotency + demo UI + **Kafbat UI** + DoD.

**Architecture:** Preserve stage 1 invariants (PG SoT, outbox+relay, evaluate≠Kafka, append-only ledger). Celery — batch/cron only (ADR-004). `usage_events` — RANGE partitions by month (ADR-011). Period close / dunning steps / grace enforcement / daily recon — Celery tasks, domain facts via outbox in the same TX.

**Tech Stack (as of plan date (2026-02-14)):** as stage 1 + Celery 5.4.x Beat/worker (not stub), Redis rate-limit keys, PostgreSQL 16 RANGE partitioning.

## Global Constraints

### From stage 1 (do not weaken)
- PostgreSQL is SoT for operational entitlements; Kafka is post-commit bus; dual-write forbidden; outbox + separate relay.
- Evaluate does not read Kafka; Redis TTL 30–60s + version bump; usage write is a separate path.
- Ledger append-only; reverse = new row.
- mock Stripe + PaymentProviderPort; no PAN; dual-id; tenant filter.
- Quality: ruff 0, mypy strict 0, unit cov ≥ 80% services+domain, integration on compose.test.
- Docs EN; ids EN; local no push; commits only on human request.

### Stage 2 additions
- `usage_events`: `PARTITION BY RANGE (recorded_at)` monthly partitions; writes on primary only.
- Celery = batch/scheduled; **idempotency on retry** required (§11.2).
- `DUNNING_ENABLED` gate; campaign pause does not mutate ledger/invoice amounts.
- Grace: access per `grace_period_days` until expiry; then revoke + entitlement bump.
- Daily recon: discrepancies + events only; **forbidden** auto-fix ledger/invoices.
- Rate limit → HTTP **429** under load/integration test.
- **Kafbat UI** in local Compose (required §11.2); local/demo only.
- **Forbidden in stage 2 tasks:** read replica, Helm HA, sharding, live Stripe, ESP.

### Design locks (brainstorming → accepted in plan)
1. **Period close** in one PG TX: snapshot aggregates → draft `invoices` + `invoice_line_items` → `LedgerService.post(usage_charge)` → outbox `usage.period_closed` (+ `ledger.entry_posted`).
2. **Grace clock:** `grace_until = past_due_entered_at + plan.grace_period_days` (store `past_due_entered_at` on subscription when entering `past_due`).
3. **Dunning schedule:** attempts on days **1 / 3 / 7** from campaign start (spec §4.3.7); notifier events in outbox; ESP outside Platform.
4. **Rate limit:** Redis fixed-window per `api_key_id` (`rl:key:{id}:{window}`); config `API_RATE_LIMIT_PER_MINUTE`.
5. **OAuth2 client credentials** — **not** in mandatory Tasks (§11.2 does not require); roadmap only.
6. **Proration** on plan change — stub: ledger `proration`/`credit` with zero or formula stub; full Stripe proration — not an S2 goal.

---

## A. Spec → stage 2 epics

| Epic | Spec | DoD §11.2 anchor | Tasks |
|------|------|-----------------|-------|
| Usage schema + partitions | §3.4, §4.3.5, §6 | partitions + create-next-month | 16 |
| Usage batch ingest | §3.4, API usage, §12.7 | batch 1000 + idempotency | 17 |
| Hourly aggregates | §3.4 | aggregates correct | 18 |
| Invoices + line items | §3.4, §6.3 | schema + API read | 19 |
| Period close + usage_charge | J2, §4.3.4 | period close → line items → ledger → mock sync | 20–21 |
| Grace engine + revoke | J3, §4.3.5 | grace until days; after revoke | 22 |
| Dunning campaigns | §4.3.7, ADR-008 | campaign on payment_failed; 1/3/7; pause | 23–24 |
| Daily recon cron + ledger↔invoice | ADR-007 amend | cron + seeded; ledger↔invoice | 25–26 |
| Plan change + proration stub | §3.4 | upgrade → entitlements | 27 |
| Rate limit 429 | §8.8, §11.2 | 429 under test | 28 |
| Celery Beat + idempotent retries | ADR-004 | retry idempotent | 29 |
| Demo UI S2 screens | §3.4, §14 | usage / recon / dunning | 31 |
| Kafbat UI (Compose) | §3.4 Kafka UI, §5, §11.2 | topics + consumer groups | 30 |
| ZDT partition migration drill | ADR-009, §8.9 | hot table expand | 32 |
| SLO/runbooks + §11.2 verification | §8.5, §11.2 | alerts + DoD | 33 |

---

## B. Global Constraints

See block above. Review Gate A always checks S1+S2 invariants.

---

## C. Dependencies from stage 1

Stage 2 plan **consumes** (does not rewrite):
- webhook processor (`invoice.payment_failed` → `past_due` + outbox);
- `LedgerService.post/reverse`, entitlements evaluate + bump;
- `OutboxService` + relay;
- manual recon (extended by cron + ledger↔invoice);
- Compose worker stub → real Celery;
- demo_ui thin client (new pages API-only);
- Kafka in compose → **Kafbat UI** (Task 30).

---

## D. Stage 2 ADR queue

1. **ADR-011** — usage_events RANGE partitioning (create before Task 16).
2. **ADR-008 amendment** — enable dunning (`DUNNING_ENABLED`, schedule 1/3/7, pause) — before Task 23.
3. **ADR-007 amendment** — daily cron + `ledger_invoice_mismatch` — before Task 25.
4. **ADR-004** — decision unchanged; Task 29 activates Beat jobs from ADR-004 list.
5. **ADR-009** — drill on detach/attach or add-column expand for hot table — Task 32.

OAuth2 / read replica — **not** stage 2 ADR (stage 3 / roadmap).

---

## E. Stage 2 tasks (Tasks 16–33)

### Task 16: usage_events RANGE partitions + next-month job

**Stage:** 2 · **Track:** domain|infra
**Cursor:** Implementer=generalPurpose; Reviewer=generalPurpose(review); Grounding=PG RANGE partitioning
**Depends on:** stage 1 schema; ADR-011
**Files:**
- Create: `domain/models/usage_event.py`, `alembic/versions/*_usage_events_partitioned.py`, `workers/tasks/create_usage_partition.py`
- Test: `tests/unit/test_usage_partition_bounds.py`, `tests/integration/test_usage_events_partition_insert.py`
**Spec:** §4.3.5 partitioning, §3.4

**Goal:** Table `usage_events` with `PARTITION BY RANGE (recorded_at)`; monthly `usage_events_YYYY_MM`; function/task to create next partition.

**Interfaces:**

```python
def month_bounds(dt: datetime) -> tuple[datetime, datetime]: ...
async def ensure_usage_partition(session, *, year: int, month: int) -> str  # partition name
```

**Steps:**

- [x] **Failing test:**

```python
def test_month_bounds_february_2026() -> None:
    start, end = month_bounds(datetime(2026, 2, 18, tzinfo=UTC))
    assert start == datetime(2026, 2, 1, tzinfo=UTC)
    assert end == datetime(2026, 3, 1, tzinfo=UTC)
```

- [x] **Run — FAIL.**
- [x] **Docs-grounding:** PostgreSQL RANGE partitioning / DETACH. Sources consulted in task-report.
- [x] **Implement** parent+partitions migration (expand-only); `ensure_usage_partition`.
- [x] **PASS** insert into current month partition.
- [x] **Review Gates A–D** (D: insert outside covered range fails loudly or auto-creates).
- [ ] **Commit** (on request): `feat: partitioned usage_events`

**Acceptance:** insert in current month works; next-month ensure idempotent.
**Risks:** UNIQUE idempotency_key globally across partitions — design in ADR-011 (index on each partition + app-level or global unique via recorded_at+key).

---

### Task 17: POST /v1/usage/events/batch (≤1000) idempotent

**Stage:** 2 · **Track:** api|domain
**Cursor:** Implementer=generalPurpose; Reviewer=generalPurpose(review); Security=security-review
**Depends on:** Task 16
**Files:**
- Create: `services/usage.py`, `api/v1/usage.py` (HTTP DTOs colocated in the route module)
- Test: `tests/unit/test_usage_batch_idempotency.py`, `tests/integration/test_usage_batch_api.py`
**Spec:** §12.7, batch 1000, §11.2

**Goal:** Tenant-scoped batch ingest; max 1000; duplicate `idempotency_key` → no second row; evaluate remains read-only.

**Interfaces:**

```python
@dataclass(frozen=True)
class UsageEventIn:
    feature_key: str
    quantity: int
    idempotency_key: str
    recorded_at: datetime | None = None

async def ingest_usage_batch(session, *, organization_id: int, events: list[UsageEventIn]) -> UsageBatchResult
# UsageBatchResult: accepted: int, duplicates: int, public_ids: list[str]
```

**Steps:**

- [x] **Failing test:**

```python
@pytest.mark.asyncio
async def test_duplicate_idempotency_key_does_not_double_insert(session, org_id):
    e = UsageEventIn(feature_key="api_calls", quantity=1, idempotency_key="u-1")
    r1 = await ingest_usage_batch(session, organization_id=org_id, events=[e])
    r2 = await ingest_usage_batch(session, organization_id=org_id, events=[e])
    assert r1.accepted == 1 and r2.duplicates == 1
    assert await count_usage(session, org_id) == 1
```

- [x] **Run — FAIL.**
- [x] **Implement** API `POST /v1/usage/events/batch`; 400 if len>1000; ON CONFLICT DO NOTHING.
- [x] **PASS** + cross-tenant 403.
- [x] **Security review** (tenant, payload size).
- [ ] **Commit** (on request): `feat: usage events batch ingest`

**Acceptance:** §11.2 batch 1000; idempotent.
**Risks:** evaluate writing usage — Gate A fail.

---

### Task 18: hourly usage aggregates (Celery)

**Stage:** 2 · **Track:** workers
**Cursor:** Implementer=generalPurpose; Reviewer=generalPurpose(review); Grounding=Celery idempotency
**Depends on:** Task 17, ADR-004
**Files:**
- Create: `domain/models/usage_aggregate.py`, `workers/tasks/aggregate_usage_hourly.py`
- Modify: `deploy/docker/Dockerfile.worker`, compose worker command → celery
- Test: `tests/unit/test_hourly_aggregate.py`, `tests/integration/test_aggregate_task_idempotent.py`
**Spec:** §3.4 aggregates

**Goal:** Table `usage_aggregates` (org, feature, hour bucket, quantity); Celery task idempotent on repeat run for same hour.

**Interfaces:**

```python
async def aggregate_hour(session, *, organization_id: int, feature_key: str, hour_start: datetime) -> UsageAggregate
# UPSERT quantity = SUM(events in [hour_start, hour_start+1h))
```

**Steps:**

- [x] **Failing test:**

```python
@pytest.mark.asyncio
async def test_aggregate_hour_idempotent(session, seeded_events):
    a1 = await aggregate_hour(session, organization_id=1, feature_key="api_calls", hour_start=HOUR)
    a2 = await aggregate_hour(session, organization_id=1, feature_key="api_calls", hour_start=HOUR)
    assert a1.quantity == a2.quantity == 42
    assert await count_aggregates(session) == 1
```

- [x] **Run — FAIL.**
- [x] **Docs-grounding:** Celery task idempotency.
- [x] **Implement** UPSERT + celery task `usage.aggregate_hourly`.
- [x] **PASS.**
- [x] **Review Gates A–D** (A: no Kafka publish from Celery).
- [ ] **Commit** (on request): `feat: hourly usage aggregates`

**Acceptance:** repeat run does not duplicate aggregate row.
**Risks:** non-idempotent INSERT-only without unique constraint.

---

### Task 19: invoices + invoice_line_items schema/API

**Stage:** 2 · **Track:** domain|api
**Cursor:** Implementer=generalPurpose; Reviewer=generalPurpose(review)
**Depends on:** Task 10 ledger patterns, orgs
**Files:**
- Create: `domain/models/invoice.py`, `services/invoices.py`, `api/v1/invoices.py`, migration
- Test: `tests/unit/test_invoice_line_item_total.py`, `tests/integration/test_invoice_list_tenant.py`
**Spec:** §6.3 Billing

**Goal:** `invoices` + `invoice_line_items` (dual-id / UUIDv7 per ADR-010 for invoice PK policy — follow §6: dual-id BIGINT+public_id); list/get by public_id; tenant isolation.

**Interfaces:**

```python
async def create_draft_invoice(session, *, organization_id: int, currency: str, period_start: datetime, period_end: datetime, idempotency_key: str) -> Invoice
async def add_line_item(session, *, invoice_id: int, description: str, quantity: int, unit_amount_cents: int, feature_key: str | None) -> InvoiceLineItem
```

**Steps:**

- [x] **Failing test:**

```python
def test_line_item_total_cents() -> None:
    assert line_total_cents(quantity=3, unit_amount_cents=100) == 300
```

- [x] **Run — FAIL.**
- [x] **Implement** models + read API + create helpers (write path used by Task 20).
- [x] **PASS.**
- [x] **Review Gates A–D** (C: no BIGINT in JSON).
- [ ] **Commit** (on request): `feat: invoices and line items`

**Acceptance:** tenant list invoices; totals computed.
**Risks:** mutating issued invoice amounts — forbidden (draft→open only via service).

---

### Task 20: period close → line items + ledger usage_charge + outbox

**Stage:** 2 · **Track:** workers|domain
**Cursor:** Implementer=generalPurpose; Reviewer=generalPurpose(review)
**Depends on:** Tasks 18–19, LedgerService
**Files:**
- Create: `services/period_close.py`, `workers/tasks/period_close.py`
- Modify: `services/ledger.py` (entry_type `usage_charge`)
- Test: `tests/unit/test_period_close_idempotent.py`, `tests/integration/test_period_close_posts_ledger.py`
**Spec:** J2, §11.2

**Goal:** `close_period(org, period)` idempotent on `idempotency_key`; one TX: invoice+lines+ledger+outbox.

**Interfaces:**

```python
async def close_billing_period(session, *, organization_id: int, period_start: datetime, period_end: datetime, idempotency_key: str) -> PeriodCloseResult
```

**Steps:**

- [x] **Failing test:**

```python
@pytest.mark.asyncio
async def test_period_close_idempotent(session, org_with_aggregates):
    r1 = await close_billing_period(session, organization_id=ORG, period_start=P0, period_end=P1, idempotency_key="pc-1")
    r2 = await close_billing_period(session, organization_id=ORG, period_start=P0, period_end=P1, idempotency_key="pc-1")
    assert r1.invoice_public_id == r2.invoice_public_id
    assert await count_ledger(session, entry_type="usage_charge") == 1
```

- [x] **Run — FAIL.**
- [x] **Implement** close + outbox `usage.period_closed` + `ledger.entry_posted`.
- [x] **PASS.**
- [x] **Review Gates A–D** (A: same TX; no Celery kafka.publish).
- [ ] **Commit** (on request): `feat: period close with usage_charge ledger`

**Acceptance:** §11.2 period close → line items → ledger.
**Risks:** partial commit without outbox.

---

### Task 21: sync invoice draft → mock Stripe

**Stage:** 2 · **Track:** integrations
**Cursor:** Implementer=generalPurpose; Reviewer=generalPurpose(review)
**Depends on:** Task 20, PaymentProviderPort
**Files:**
- Modify: `integrations/payment_provider.py`, `integrations/mock_stripe/*`, `services/period_close.py`
- Test: `tests/integration/test_invoice_sync_mock_stripe.py`
**Spec:** §3.4 invoicing sync

**Goal:** After period close (or separate step in same orchestration) create/update invoice in mock Stripe registry; store `external_invoice_id`.

**Interfaces:**

```python
class PaymentProviderPort(Protocol):
    async def create_invoice(self, *, customer_id: str, amount_cents: int, currency: str, idempotency_key: str) -> str: ...
```

**Steps:**

- [x] **Failing test:** port method missing / sync leaves external id.
- [x] **Implement** mock endpoint + client; wire after close (still: Stripe call **after** PG commit of domain OR record intent in outbox consumer — **prefer:** store local invoice first; sync via Celery `invoices.sync_mock_stripe` reading local row; on success UPDATE only `external_invoice_id`/`synced_at` columns allowed as non-amount metadata).
- [x] Document in task-report: amount fields immutable; only sync metadata updated.
- [x] **PASS.**
- [x] **Review Gate A** (no amount mutation).
- [ ] **Commit** (on request): `feat: sync invoices to mock stripe`

**Acceptance:** mock registry contains matching amount.
**Risks:** treating Stripe as SoT.

---

### Task 22: grace policy engine + enforce expiry

**Stage:** 2 · **Track:** domain|workers
**Cursor:** Implementer=generalPurpose; Reviewer=generalPurpose(review)
**Depends on:** Task 7 past_due path, Task 9 entitlements
**Files:**
- Create: `services/grace.py`, `workers/tasks/enforce_grace_expiry.py`
- Modify: `domain/models/subscription.py` (`past_due_entered_at`), `services/entitlements.py`, `webhook_processor.py`
- Test: `tests/unit/test_grace_until.py`, `tests/unit/test_past_due_grace_degraded.py` (extend), `tests/integration/test_grace_expiry_revoke.py`
**Spec:** J3, §11.2 grace

**Goal:** On entering `past_due` write `past_due_entered_at`; `grace_until`; evaluate accounts for grace; Celery transitions to `unpaid`/`canceled` per SM + revoke + bump + outbox `subscription.access_revoked`.

**Interfaces:**

```python
def compute_grace_until(*, past_due_entered_at: datetime, grace_period_days: int) -> datetime: ...
async def enforce_grace_expiry(session, *, now: datetime) -> int  # processed count
```

**Steps:**

- [x] **Failing test:**

```python
def test_grace_until_plus_seven_days() -> None:
    entered = datetime(2026, 2, 16, tzinfo=UTC)
    assert compute_grace_until(past_due_entered_at=entered, grace_period_days=7) == datetime(2026, 2, 23, tzinfo=UTC)
```

- [x] **Run — FAIL.**
- [x] **Implement** + wire webhook past_due; Celery beat daily.
- [x] **PASS:** within grace → not revoked; after → revoked + bump.
- [x] **Review Gates A–D.**
- [ ] **Commit** (on request): `feat: grace policy and expiry enforcement`

**Acceptance:** §11.2 grace bullets.
**Risks:** revoke without cache bump.

---

### Task 23: dunning campaigns on payment_failed

**Stage:** 2 · **Track:** domain
**Cursor:** Implementer=generalPurpose; Reviewer=generalPurpose(review); Security=security-review (operator role later)
**Depends on:** Task 7, ADR-008 amendment, `DUNNING_ENABLED`
**Files:**
- Create: `domain/models/dunning.py`, `services/dunning.py`, migration
- Modify: `webhook_processor.py` (if enabled → create campaign)
- Test: `tests/unit/test_dunning_campaign_create.py`, `tests/integration/test_payment_failed_starts_dunning.py`
**Spec:** §4.3.7, §11.2

**Goal:** On `invoice.payment_failed` and `settings.dunning_enabled` create `dunning_campaigns` (unique per subscription active campaign); when `false` — no-op (S1 behavior).

**Interfaces:**

```python
async def start_campaign(session, *, subscription_id: int, organization_id: int, idempotency_key: str) -> DunningCampaign | None
```

**Steps:**

- [x] **Failing test:** enabled → one campaign; disabled → None; duplicate key → one row.
- [x] **Implement.**
- [x] **PASS.**
- [x] **Review.**
- [ ] **Commit** (on request): `feat: dunning campaign on payment_failed`

**Acceptance:** campaign created only when flag on.
**Risks:** creating campaigns when disabled.

---

### Task 24: dunning attempts 1/3/7 + pause/resume + events

**Stage:** 2 · **Track:** workers|api
**Cursor:** Implementer=generalPurpose; Reviewer=generalPurpose(review); Security=security-review
**Depends on:** Task 23
**Files:**
- Create: `workers/tasks/dunning_steps.py`, `api/v1/admin/dunning.py`
- Test: `tests/unit/test_dunning_schedule.py`, `tests/integration/test_dunning_pause_resume.py`
**Spec:** §4.3.7 days 1/3/7; pause

**Goal:** Schedule attempts; Celery executes due attempts (mock Stripe retry + outbox `dunning.*`); admin pause/resume; pause skips execution without deleting attempts.

**Interfaces:**

```python
def schedule_attempt_offsets_days() -> tuple[int, int, int]:
    return (1, 3, 7)

async def pause_campaign(session, *, campaign_public_id: uuid.UUID, actor_key_id: int) -> DunningCampaign
async def resume_campaign(session, *, campaign_public_id: uuid.UUID, actor_key_id: int) -> DunningCampaign
async def process_due_attempts(session, *, now: datetime) -> int
```

**Steps:**

- [x] **Failing test:** offsets (1,3,7); paused campaign → process_due returns 0 side effects.
- [x] **Implement** + outbox events; **no ledger amount UPDATE**.
- [x] **PASS.**
- [x] **Security:** platform_admin or `dunning_operator` only.
- [ ] **Commit** (on request): `feat: dunning attempts and pause`

**Acceptance:** §11.2 dunning bullet.
**Risks:** ESP coupling; mutating invoice totals.

---

### Task 25: daily reconciliation cron + ledger↔invoice

**Stage:** 2 · **Track:** workers|domain
**Cursor:** Implementer=generalPurpose; Reviewer=generalPurpose(review)
**Depends on:** Task 11, Task 19–20, ADR-007 amendment
**Files:**
- Modify: `services/reconciliation.py`, `workers/tasks/reconciliation_daily.py`
- Test: `tests/unit/test_ledger_invoice_mismatch.py`, `tests/integration/test_daily_recon_cron.py`
**Spec:** §4.3.6 step 4, §11.2

**Goal:** Celery Beat `0 2 * * *` runs recon; emit `ledger_invoice_mismatch` when sums diverge; still no auto-fix.

**Interfaces:**

```python
def compare_ledger_to_invoice(*, ledger_total_cents: int, invoice_total_cents: int) -> DiscrepancyDraft | None
```

**Steps:**

- [x] **Failing test:**

```python
def test_ledger_invoice_mismatch_detected() -> None:
    d = compare_ledger_to_invoice(ledger_total_cents=1000, invoice_total_cents=900)
    assert d is not None and d.kind == "ledger_invoice_mismatch"
```

- [x] **Implement** + cron task + outbox `reconciliation.mismatch` when needed.
- [x] **PASS** seeded case.
- [x] **Review Gate D:** re-run idempotent / no ledger mutation.
- [ ] **Commit** (on request): `feat: daily recon with ledger-invoice compare`

**Acceptance:** cron finds seeded discrepancy; ledger↔invoice covered.
**Risks:** auto-fix.

---

### Task 26: recon / dunning / outbox alert hooks (docs + metric stubs)

**Stage:** 2 · **Track:** ops
**Cursor:** Implementer=generalPurpose; Reviewer=generalPurpose(review)
**Depends on:** Task 25, docs/slo.md
**Files:**
- Modify: `docs/slo.md`, runbooks; optional `metrics.py` counters
- Test: `tests/unit/test_alert_threshold_helpers.py`
**Spec:** §8.5, §11.2 SLO/alerts

**Goal:** Formalize alert conditions from `docs/slo.md`; expose counters (`outbox_unpublished_count`, `reconciliation_discrepancy_amount_cents`); wire runbook links; no external PagerDuty required in S2.

**Steps:**

- [x] **Failing test:** threshold helper for recon alert amount.
- [x] **Implement** helpers + document scrape targets.
- [x] **PASS.**
- [ ] **Commit** (on request): `chore: slo alert stubs for stage2`

**Acceptance:** §11.2 SLO/alerts + runbooks including dunning-stuck.
**Risks:** claiming production paging without exporter.

---

### Task 27: plan change upgrade/downgrade + stub proration + entitlement bump

**Stage:** 2 · **Track:** domain|api
**Cursor:** Implementer=generalPurpose; Reviewer=generalPurpose(review)
**Depends on:** Tasks 4–5, 9–10
**Files:**
- Create: `services/plan_change.py`, `api/v1/subscriptions.py` (change endpoint)
- Test: `tests/unit/test_plan_change_immediate.py`, `tests/integration/test_upgrade_bumps_entitlements.py`
**Spec:** §3.4 plan change, §11.2 upgrade

**Goal:** Immediate upgrade to published plan; stub proration ledger entries; bump entitlement version after commit.

**Interfaces:**

```python
async def change_plan(session, *, subscription: Subscription, new_plan_id: uuid.UUID, effective: Literal["immediate"], idempotency_key: str) -> Subscription
```

**Steps:**

- [x] **Failing test:** upgrade changes plan_id; posts stub proration; illegal when canceled.
- [x] **Implement** + outbox `subscription.plan_changed`.
- [x] **PASS** + evaluate reflects new features after bump.
- [ ] **Review.**
- [ ] **Commit** (on request): `feat: subscription plan change with stub proration`

**Acceptance:** upgrade immediately updates entitlements.
**Risks:** in-place mutate published catalog.

---

### Task 28: API key rate limiting → 429

**Stage:** 2 · **Track:** api|security
**Cursor:** Implementer=generalPurpose; Reviewer=generalPurpose(review); Security=security-review; Grounding=Redis rate limit
**Depends on:** Task 3 auth
**Files:**
- Create: `middleware/rate_limit.py` or `services/rate_limit.py`
- Modify: `api/deps.py` / middleware stack
- Test: `tests/integration/test_rate_limit_429.py`
**Spec:** §11.2 rate limit

**Goal:** Exceeding `API_RATE_LIMIT_PER_MINUTE` returns **429** with Retry-After; health endpoints excluded.

**Interfaces:**

```python
async def check_rate_limit(redis, *, api_key_id: int, limit_per_minute: int) -> RateLimitDecision
# allowed: bool, remaining: int, retry_after_seconds: int | None
```

**Steps:**

- [x] **Failing test:** N+1st request → 429.
- [x] **Docs-grounding:** Redis INCR/EXPIRE fixed window.
- [x] **Implement.**
- [x] **PASS.**
- [x] **Security review.**
- [ ] **Commit** (on request): `feat: api key rate limiting returns 429`

**Acceptance:** 429 under test.
**Risks:** limiting `/health/live`.

---

### Task 29: Celery Beat schedule + retry idempotency suite

**Stage:** 2 · **Track:** workers
**Cursor:** Implementer=generalPurpose; Reviewer=generalPurpose(review)
**Depends on:** Tasks 18, 20, 22, 24, 25
**Files:**
- Create: `workers/celery_app.py`, `workers/beat_schedule.py`
- Modify: compose worker/beat services
- Test: `tests/unit/test_celery_tasks_idempotent.py`
**Spec:** §11.2 Celery idempotent retry

**Goal:** Single beat schedule; parametrized tests that invoking each task twice with same business key is safe.

**Steps:**

- [x] **Failing test:** matrix over task names assert idempotent.
- [x] **Implement** schedule: hourly aggregate, daily grace, daily recon 02:00, dunning due, create partition monthly.
- [x] **PASS.**
- [x] **Review Gate A:** no outbox publish from beat directly.
- [ ] **Commit** (on request): `feat: celery beat schedules and idempotent retries`

**Acceptance:** §11.2 Celery bullet.
**Risks:** beat publishing Kafka.

---

### Task 30: Kafbat UI in Docker Compose

**Stage:** 2 · **Track:** infra|ops
**Cursor:** Implementer=generalPurpose; Reviewer=generalPurpose(review); Grounding=Kafbat UI compose
**Depends on:** stage 1 Kafka in compose; ADR-002
**Files:**
- Modify: `deploy/compose/docker-compose.yml` (or root compose) — service `kafbat-ui`
- Create/Modify: `.env.example` (`KAFBAT_UI_PORT` etc.), `README.md` (UI link/port)
- Test: `tests/integration/test_kafbat_ui_compose.py` **or** checklist + curl health in task-report
**Spec:** §3.4 Kafka UI, §5 Kafbat, §11.2

**Goal:** Bring up **Kafbat UI** (`ghcr.io/kafbat/kafka-ui`) next to local Kafka: view billing topics, messages, consumer groups (including outbox-relay). Only local/demo; without public expose without auth.

**Interfaces:**

```yaml
# compose (illustrative)
services:
  kafbat-ui:
    image: ghcr.io/kafbat/kafka-ui:latest  # pin digest/tag in implementation
    ports: ["8080:8080"]  # or KAFBAT_UI_PORT
    environment:
      KAFKA_CLUSTERS_0_NAME: local
      KAFKA_CLUSTERS_0_BOOTSTRAPSERVERS: kafka:9092
```

**Steps:**

- [x] **Failing check:** `compose config` / service health URL missing.
- [x] **Docs-grounding:** https://ui.docs.kafbat.io/ or ghcr.io/kafbat/kafka-ui quick start; Sources consulted in task-report.
- [x] **Implement** service + env; do not put secrets in repo.
- [x] **PASS:** UI responds; topics visible from `init-kafka-topics` (or created by relay); relay consumer group visible after publish.
- [x] **Review Gate C:** UI not on public prod path; port localhost/docs only.
- [ ] **Commit** (on request): `feat: add kafbat ui to local compose`

**Acceptance:** §11.2 Kafbat bullet; README lists URL.
**Risks:** `latest` tag drift — pin version; confusion with demo-ui port.

---

### Task 31: demo_ui — usage, recon runs, dunning card

**Stage:** 2 · **Track:** ui
**Cursor:** Implementer=generalPurpose; Reviewer=generalPurpose(review)
**Depends on:** Tasks 17, 25, 24 APIs
**Files:**
- Create: `demo_ui/src/pages/UsagePage.tsx`, `ReconciliationPage.tsx`, `DunningPage.tsx`
- Modify: nav/router
- Test: manual smoke checklist in task-report
**Spec:** §3.4 UI, §14 thin client

**Goal:** Display-only pages calling OpenAPI; no billing logic in UI; secrets runtime-injected (Gate C). Kafbat — separate Task 30 (do not embed Kafka iframe in demo SPA without need).

**Steps:**

- [x] **Failing check:** pages missing.
- [x] **Scaffold** pages + fetch.
- [x] **Smoke:** seed usage → chart/list; recon run list; dunning campaign status.
- [x] **Review Gate C.**
- [ ] **Commit** (on request): `feat: demo ui usage recon dunning`

**Acceptance:** §11.2 UI bullet.
**Risks:** business logic in client.

---

### Task 32: zero-downtime migration drill (hot table)

**Stage:** 2 · **Track:** infra
**Cursor:** Implementer=generalPurpose; Reviewer=generalPurpose(review); Grounding=expand/contract
**Depends on:** ADR-009, Task 16
**Files:**
- Create: `alembic/versions/*_zdt_drill_*.py`, `docs/runbooks/migration-zdt-usage.md`
- Test: `tests/integration/test_zdt_migration_expand.py`
**Spec:** §8.9, §11.2 migration bullet

**Goal:** Apply expand-only change on hot path (e.g. nullable sync metadata column on invoices **or** documented partition create online); document expand→migrate→contract; contract deferred.

**Steps:**

- [x] **Failing test:** migration upgrade adds column/partition without exclusive lock assumption documented.
- [x] **Implement** + runbook.
- [x] **PASS** upgrade/downgrade expand step.
- [ ] **Review.**
- [ ] **Commit** (on request): `chore: zdt migration drill for stage2`

**Acceptance:** §11.2 ZDT applied to one hot table.
**Risks:** breaking DDL with code deploy same step.

---

### Task 33: Stage 2 DoD verification + README/runbooks close

**Stage:** 2 · **Track:** docs|qa
**Cursor:** Implementer=generalPurpose; Reviewer=generalPurpose(review); Skills=`verification-before-completion`
**Depends on:** Tasks 16–32
**Files:**
- Modify: `README.md` (S2 demo path + Kafbat URL), `docs/runbooks/dunning-stuck.md`, `docs/progress.md`
- Test: orchestrator runs §11.2 matrix (including Kafbat)
**Spec:** §11.2

**Goal:** Fresh evidence for every §11.2 checkbox; human checkpoint **#4** (Stage2 Done).

**Steps:**

- [x] **Write** S2 README section (demo-ui + kafbat-ui).
- [x] **Finalize** dunning-stuck runbook (remove «N/A only»).
- [x] **Run** verification matrix; record evidence in `docs/progress.md` (screenshot/curl Kafbat topics).
- [x] **Review Gates A–D.**
- [ ] **Human checkpoint #4** — do not declare Stage2 Done without “accept”.
- [ ] **Commit** (on request): `docs: stage2 demo path and dod evidence`

**Acceptance:** §11.2 all PASS with evidence.
**Risks:** Done without verification-before-completion.

---

## F. Stage 3 roadmap (not Tasks)

- Read replica for evaluate/reports
- Helm HA + relay leader election
- API key rotation without downtime; stronger limits
- OAuth2 client credentials
- Load profiles §8.1.1 / §10.5 (12k evaluate / mixed 15–20k) — **not** stage 2 Task
- Prometheus/Grafana ADR Adopt|Defer (§8.5.1)
- DLQ replay tooling hardened
- **No sharding** (criteria ADR only)

---

## G. Test strategy

| Level | Command | Threshold |
|---------|---------|-------|
| Lint/types/unit | `make lint typecheck test-unit` | as S1 + new services |
| Integration | `make test-integration` | compose.test + Celery where needed |
| S2 acceptance | pytest list in Task 33 | 100% §11.2 |

---

## H. Risks → runbook → Task

| Failure | Runbook | Task |
|---------|---------|------|
| Usage partition missing | migration-zdt-usage / ops note | 16, 32 |
| Period close double charge | — (idempotency key) | 20 |
| Dunning stuck | `dunning-stuck.md` | 24, 33 |
| Recon mismatch | `reconciliation-mismatch.md` | 25 |
| Rate limit false positives | slo / config | 28 |
| Outbox lag | `outbox-lag.md` | (S1) + 29 |
| Kafbat unavailable / port taken | README compose ports | 30 |

---

## I. Calendar 6–8 weeks + checkpoints

| Weeks | Focus | Tasks | Phase |
|--------|--------|-------|------|
| 1–2 | Partitions + ingest + aggregates | 16–18 | PHASE_6 |
| 2–3 | Invoices + period close + Stripe sync | 19–21 | PHASE_7 · **CP-S2-0** after 21 |
| 3–4 | Grace + dunning | 22–24 | PHASE_8 · **CP-S2-A** after 24 |
| 4–5 | Daily recon + alerts | 25–26 | PHASE_9 |
| 5–6 | Plan change + rate limit | 27–28 | PHASE_10 |
| 6–7 | Celery Beat + Kafbat + demo UI + ZDT | 29–32 | PHASE_11 |
| 7–8 | DoD §11.2 | 33 | PHASE_11 · **CP-S2-B / #4** |

**Stage 2 human checkpoints:**
- **CP-S2-0:** after Task 21 (usage → aggregates → period close → invoice/ledger/mock sync).
- **CP-S2-A:** after Task 24 (grace + dunning vertical).
- **CP-S2-B (#4):** after Task 33 / §11.2 verification — Stage2 Done only with “accept”.

---

## Docs-grounding (Sources consulted)

| Topic | URL | Plan takeaway |
|------|-----|-----------------|
| Celery Beat | https://docs.celeryq.dev/en/stable/userguide/periodic-tasks.html | Single Beat per schedule; `crontab` + `beat_schedule`; overlap → locking/idempotency in Tasks 18/25/29. |
| PG partitioning | https://www.postgresql.org/docs/16/ddl-partitioning.html | RANGE parent + child bounds; UNIQUE local to partition; DETACH for archive (ADR-011 / Task 16/32). |
| Redis rate limit | https://redis.io/docs/latest/commands/incr/ + https://redis.io/docs/latest/develop/use-cases/rate-limiter/ | Fixed-window: `INCR` + `EXPIRE` atomically (Lua/`EVAL`); key per api_key + window (Task 28). |
| Stripe invoice retries (mock) | https://docs.stripe.com/invoicing/overview (semantics) | Retry/dunning — external semantics; in Platform mock port + outbox, without live SDK (Tasks 21/24). |
| OTel metrics | https://opentelemetry.io/docs/concepts/signals/metrics/ | Counters/histograms for SLO stubs (Task 26); alerts — docs + metric hooks, not mandatory remote exporter. |
| Kafbat UI | https://github.com/kafbat/kafka-ui + https://www.kafbat.io/ | Compose: `ghcr.io/kafbat/kafka-ui`; bootstrap servers → local Kafka; Task 30. |

---

## J. Plan DoD (spec.md §11.2)

- [ ] batch 1000 usage; hourly aggregates correct
- [ ] period close → line items → ledger usage_charge → sync mock Stripe
- [ ] recon cron finds seeded discrepancy; mismatch event in Kafka
- [ ] recon includes ledger ↔ invoice
- [ ] grace: access not revoked until `grace_period_days`; after — revoke
- [ ] dunning: campaign on payment_failed; attempts on schedule; pause/resume
- [ ] upgrade immediately updates entitlements
- [ ] Celery tasks idempotent on retry
- [ ] rate limit returns 429 under load test
- [ ] SLI/SLO in `docs/slo.md`; alerts + runbooks: outbox-lag, webhook-replay, reconciliation-mismatch, dunning-stuck
- [ ] zero-downtime migration plan applied to at least one hot table
- [ ] UI: usage, recon runs, dunning card
- [ ] Kafbat UI in Compose: billing topics + consumer groups visible (spec.md §11.2 / §3.4)

---

## Orchestration

| Role | Mechanism |
|------|----------|
| Orchestrator | parent session |
| Implementer | fresh `generalPurpose` per Task |
| Reviewer | another `generalPurpose` |
| Security | usage ingest, dunning admin, rate limit |
| Grounding | Celery, PG partitions, Redis RL, Kafbat UI |

**Execution start:**

```text
Stage 2 plan accepted. Continue per [`AGENTS.md` §10.3](../../AGENTS.md#103-stage-2--usage-reconciliation-dunning-specmd-§34--§112) (PHASE_6 + stage 2 plan + ADR-011).
Locally, without push; commits only on my command. Implementer ≠ Reviewer.
```
