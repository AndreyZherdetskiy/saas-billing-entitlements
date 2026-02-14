# Implementation plan: Billing and entitlements platform (stage 1)

> **For agentic workers:** REQUIRED SUB-SKILL: `superpowers:subagent-driven-development`.
> Implementer ≠ Reviewer. Local: no push. Checklists `- [ ]`.
> Execute phases per [`AGENTS.md` §10.2](../../AGENTS.md#102-stage-1--foundation-specmd-§33--§111) (+ Stage 1 plan; phase prompts in §10.1).

**Goal:** End-to-end Foundation MVP: organization → trial/subscription → entitlements → mock Stripe webhook → transactional outbox → Kafka (≥5 event types) → minimal ledger → manual reconciliation; Compose + thin demo_ui; DoD §11.1.

**Architecture:** PostgreSQL is the source of truth for operational entitlements; domain + ledger + `outbox_messages` in one TX; separate `outbox-relay` (aiokafka) publishes to Kafka; evaluate = Redis cache → PG (not Kafka); mock Stripe via `PaymentProviderPort`; dual-id on hot entities.

**Tech Stack (as of plan date (2026-02-14)):** Python 3.12.x, FastAPI 0.115.x, Uvicorn 0.32.x, Pydantic 2.10.x, SQLAlchemy 2.0.x async + asyncpg 0.30.x, Alembic 1.14.x, PostgreSQL 16.x, Redis 7.4.x, Kafka 3.7.x KRaft, aiokafka, Celery 5.4.x (stubs S1), structlog 24.x, OpenTelemetry 1.28.x, pytest 8.x / pytest-asyncio 0.24.x, httpx 0.28.x, Ruff 0.8.x, mypy 1.13.x strict, uv 0.5.x, Docker Compose v2, demo_ui Vite+React+TypeScript.

## Global Constraints

- PostgreSQL — SoT for operational entitlements; Kafka — integration boundary (facts after commit).
- Dual-write forbidden: transactional outbox + separate outbox relay only (not Celery-publish of domain facts).
- Entitlement evaluation does not read Kafka; Redis cache TTL 30–60s + version invalidation.
- Ledger — append-only; reversal = new entry with `reverses_entry_id`.
- Stage 1: mock Stripe + PaymentProviderPort; without PAN/PCI in our DB.
- dual-id: BIGINT identity inside + public_id UUIDv7 outside (catalog = UUIDv7 PK; outbox = BIGINT without public_id).
- Tenant isolation: all requests filtered by organization.
- Local quality gates: ruff 0, mypy strict 0, unit coverage ≥ 80% (services+domain), integration on compose.test.
- Repo structure — spec §9; Stage 1 DoD — §11.1; mandatory tests — §10.2.
- Prose in docs — English technical; code identifiers — as-is.
- FORBIDDEN without explicit human command: `git push`, `gh pr create`, remote deploy, registry publish.
- Commits only when human asks; commit steps exist in tasks but are not part of automatic agent DoD.

---

## A. Spec → epics

| Epic | spec | DoD anchor |
|------|-----|-----------|
| 0 Bootstrap tooling + AGENTS/rules/docs | §9 | pyproject/uv, AGENTS, rules, ADR |
| 1 Compose skeleton | §4.2, §9 | api/pg/redis/kafka/mock-stripe; worker/relay stubs |
| 2 dual-id + Alembic baseline | §6.2, §12.14, ADR-010 | `alembic upgrade head` < 60s |
| 3 organizations + api_keys + tenant | §6.3, §8.3, API A | cross-tenant → 403 |
| 4 catalog + publish | §6.3, API B | snapshot after publish |
| 5 subscriptions + state machine | App. A, API C | create/cancel; illegal transitions |
| 6 PaymentProviderPort + mock Stripe | §5.1, ADR-005 | HMAC; persist-first webhook endpoint |
| 7 webhook processor + idempotency | §6.4, §8.2 | duplicate → no 2nd outbox/ledger |
| 8 outbox + relay → Kafka ≥5 types | ADR-001/002/004 | envelope v1; SKIP LOCKED |
| 9 entitlement evaluator + Redis | ADR-003, API D | cache_hit; invalidate < 60s |
| 10 ledger minimal postings | ADR-006 | pay/activate; reversal |
| 11 reconciliation manual + seeded discrepancy | ADR-007, API G | discrepancy row |
| 12 health/ready/live + graceful shutdown | §8.6 | ready fails without DB |
| 13 Makefile local CI | §10.3–10.4 | lint/type/unit/integration |
| 14 thin demo_ui | §14 | org/sub/entitlements/webhook |
| 15 README demo < 15 min + runbooks | §11.1, App. C | happy path documented |

---

## B. Global Constraints

See block above. Each task implicitly includes Global Constraints. Review Gate A always checks invariants.

---

## C. Stage 0 Bootstrap (before domain)

Tasks 0–1 + ADR-first (section D). Domain code starts at Task 2 (schema) / Task 3 (orgs).

---

## D. ADR-first queue (blocks code)

Write/accept order before domain implementation:

1. **ADR-010** identifier policy — dual-id / catalog / outbox schema
2. **ADR-001** transactional outbox
3. **ADR-004** Celery vs outbox-relay
4. **ADR-002** Kafka as integration bus
5. **ADR-005** PaymentProviderPort + mock Stripe
6. **ADR-006** append-only ledger
7. **ADR-003** entitlement cache (before Task 9)
8. **ADR-007** reconciliation (before Task 11); **ADR-008** dunning S2 scope; **ADR-009** zero-downtime migrations

**Human checkpoint #1:** after Task 0 (Bootstrap). Critical ADRs (001, 002, 004, 005, 006, 010) already Accepted in Part 0 — not part of this stop. Phase canon: [`AGENTS.md` §10.2](../../AGENTS.md#102-stage-1--foundation-specmd-§33--§111) (PHASE_0 → stop → PHASE_1).

---

## E. Stage 1 tasks

### Task 0: Bootstrap tooling + agentic docs sync

**Stage:** 1
**Track:** bootstrap|docs
**Cursor:** Implementer=generalPurpose; Reviewer=generalPurpose(review); Skills=`writing-plans` (already), `verification-before-completion`
**Depends on:** —
**Files:**
- Create: `pyproject.toml`, `.python-version`, `.gitignore`, `.env.example`, `Makefile` (stubs), `src/billing_platform/__init__.py`, `tests/__init__.py`, `tests/conftest.py`
- Verify present: `AGENTS.md`, `docs/adr/*`, `docs/agentic/*`
**TZ:** §5, §9

**Goal:** Initialize Python package `billing_platform` on uv with ruff/mypy/pytest; local git init without remote; lock tooling.

**Interfaces:**
- Consumes: —
- Produces: package `billing_platform` importable; `uv sync` works; `ruff`/`mypy`/`pytest` entrypoints in `pyproject.toml`

**Steps:**

- [x] **Failing test:** create `tests/unit/test_package_import.py`:

```python
def test_billing_platform_importable() -> None:
    import billing_platform

    assert billing_platform.__version__ == "0.1.0"
```

- [x] **Run test — expected FAIL**

```bash
cd /home/andrey_py_dev/Dev/_real_projects/1_saas_billing_entitlements
uv run pytest tests/unit/test_package_import.py -v
```

Expected: FAIL (`ModuleNotFoundError` or no `__version__`).

- [x] **ADR/docs:** ensure `docs/adr/001`–`010` and `AGENTS.md` present (created in plan chat); do not duplicate contradictions.

- [x] **Docs-grounding + Sources consulted:** uv workflow
  Sources: https://docs.astral.sh/uv/ — `uv init`/`uv add`/`uv lock`/`uv sync`; Python 3.12 pin.

- [x] **Minimal implementation:**
  - `git init` (without remote)
  - `.python-version` → `3.12`
  - `pyproject.toml`: name `billing-platform`, packages `src/billing_platform`, deps FastAPI/SQLAlchemy/… per §5; tool.ruff, tool.mypy strict, tool.pytest
  - `src/billing_platform/__init__.py` with `__version__ = "0.1.0"`
  - `.gitignore`: `.venv/`, `.env`, `__pycache__/`, `.mypy_cache/`, `.ruff_cache/`, `dist/`, `node_modules/`
  - `.env.example` with keys from App. B (empty values)
  - `uv lock && uv sync`

- [x] **Run tests — PASS**

```bash
uv run pytest tests/unit/test_package_import.py -v
uv run ruff check src tests
uv run mypy src
```

Expected: PASS / 0 errors.

- [x] **Update AGENTS.md** if tooling paths changed. (not required)

- [x] **Independent review Gates A–D** (separate Reviewer subagent). **APPROVE**

- [ ] **Commit step** (only on human request): `chore: bootstrap uv package and tooling`

**Acceptance:** `uv sync` + import + ruff/mypy green on empty package; no secrets in git.
**Risks:** Poetry drift — forbidden; only uv.

---

### Task 1: Compose skeleton (api/postgres/redis/kafka/mock-stripe; worker/relay stubs)

**Stage:** 1
**Track:** infra
**Cursor:** Implementer=generalPurpose; Reviewer=generalPurpose(review); Grounding=FastAPI lifespan; Skills=`systematic-debugging` on red compose
**Depends on:** Task 0
**Files:**
- Create: `deploy/compose/docker-compose.yml`, `deploy/compose/docker-compose.test.yml`, `deploy/compose/init-kafka-topics.sh`, `deploy/docker/Dockerfile.api`, `deploy/docker/Dockerfile.worker`, `deploy/docker/Dockerfile.outbox-relay`, `deploy/docker/Dockerfile.mock-stripe`, `src/billing_platform/main.py`, `src/billing_platform/config.py`, `src/billing_platform/logging.py`
**TZ:** §4.2, §9, App. B

**Goal:** `docker compose -f deploy/compose/docker-compose.yml up` brings up postgres, redis, kafka, mock-stripe stub, api stub (`/health/live` → 200), worker/relay stubs.

**Interfaces:**
- Consumes: Task 0 package
- Produces: `Settings` from env; `create_app()` FastAPI; compose service names: `postgres`, `redis`, `kafka`, `mock-stripe`, `billing-api`, `billing-worker`, `outbox-relay`

**Steps:**

- [x] **Failing test:** `tests/integration/test_compose_health.py` (skip if no compose; else):

```python
import httpx
import pytest

@pytest.mark.integration
def test_api_live_returns_200() -> None:
    r = httpx.get("http://localhost:8000/health/live", timeout=5.0)
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
```

- [x] **Run — FAIL** (no service / 404).

- [x] **Docs-grounding:** FastAPI lifespan + graceful shutdown
  Sources: https://fastapi.tiangolo.com/advanced/events/ (lifespan); Uvicorn signal handling.

- [x] **Minimal implementation:**
  - `config.py`: pydantic-settings `Settings` with `DATABASE_URL`, `REDIS_URL`, `KAFKA_BOOTSTRAP_SERVERS`, …
  - `main.py`:

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI

@asynccontextmanager
async def lifespan(app: FastAPI):
    yield

def create_app() -> FastAPI:
    app = FastAPI(title="Billing Platform", lifespan=lifespan)

    @app.get("/health/live")
    async def live() -> dict[str, str]:
        return {"status": "ok"}

    return app

app = create_app()
```

  - Compose (as of plan date (2026-02-14)): PG 16, Redis 7.4, Kafka KRaft 3.7, mock-stripe nginx/python stub on `:8001`, api build from Dockerfile.api
  - `init-kafka-topics.sh`: create `billing.subscription.events`, `billing.invoice.events`, `billing.ledger.events`, `billing.reconciliation.events`, `billing.entitlement.events`, `billing.dlq`
  - worker/relay: sleep-loop stubs or `python -c "…"` entrypoints

- [x] **Run:**

```bash
docker compose -f deploy/compose/docker-compose.yml up -d --build
curl -s http://localhost:8000/health/live
```

Expected: `{"status":"ok"}`.

- [x] **Review Gates A–D** — **APPROVE** (after fix: skip-on-ConnectError + compose `${VAR:-default}`)
- [ ] **Commit** (on request): `chore: add compose skeleton and live probe`

**Acceptance:** all services healthy; api live; topics script exists.
**Risks:** Kafka KRaft image drift — pin image tag.

**Parallel:** stub mock-stripe image can proceed in parallel with Task 2 after merge Compose; sync before Task 6.

---

### Task 2: dual-id + Alembic baseline

**Stage:** 1
**Track:** domain|infra
**Cursor:** Implementer=generalPurpose; Reviewer=generalPurpose(review); Grounding=SQLAlchemy 2 async + Alembic expand/contract
**Depends on:** Task 1, ADR-010
**Files:**
- Create: `alembic.ini`, `alembic/env.py`, `alembic/versions/20260216_0001_baseline.py`, `src/billing_platform/domain/models/base.py`, `src/billing_platform/domain/models/organization.py` (skeleton), `src/billing_platform/db.py`
- Test: `tests/unit/test_id_policy.py`, `tests/integration/test_alembic_upgrade.py`
**TZ:** §6.2, §12.14, ADR-010, §8.9

**Goal:** Baseline schema helpers: dual-id mixin; Alembic upgrade on empty PG < 60s; outbox BIGINT without public_id; catalog UUIDv7 PK.

**Interfaces:**
- Consumes: compose postgres, Settings.DATABASE_URL
- Produces:

```python
class DualIdMixin:
    id: Mapped[int]  # BIGINT IDENTITY
    public_id: Mapped[uuid.UUID]  # UUIDv7 UNIQUE

async def get_session() -> AsyncIterator[AsyncSession]: ...
```

**Steps:**

- [x] **Failing test:**

```python
from billing_platform.domain.ids import generate_uuidv7

def test_uuidv7_is_version_7() -> None:
    u = generate_uuidv7()
    assert u.version == 7
```

- [x] **Run — FAIL** (`ModuleNotFoundError`).

- [x] **Docs-grounding:** SQLAlchemy 2 async session / no lazy-load; Alembic expand/contract
  Sources: https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html ; https://alembic.sqlalchemy.org/en/latest/

- [x] **Minimal implementation:** `generate_uuidv7()` (uuid6/uuid7 lib or manual generation); `DualIdMixin`; `Base`; alembic env async; migration creates minimum `organizations` (dual-id) + `outbox_messages` (BIGINT PK, without public_id) as scaffold (full tables can grow in Tasks 3–11, but baseline must reflect ID policy).

- [x] **Run:**

```bash
uv run pytest tests/unit/test_id_policy.py -v
docker compose -f deploy/compose/docker-compose.yml exec -T billing-api alembic upgrade head
# or locally: DATABASE_URL=... uv run alembic upgrade head
```

Expected: PASS; upgrade < 60s.

- [x] **Review Gates A–D** (especially D: migration safety — expand only in baseline) — **APPROVE** (after fix: skip without Docker)
- [ ] **Commit** (on request): `feat: alembic baseline with dual-id policy`

**Acceptance:** ADR-010 followed in DDL; BIGINT does not leak to public DTOs (rule in AGENTS).
**Risks:** UUIDv4 as PK — forbidden.

---

### Task 3: organizations + api_keys + tenant isolation

**Stage:** 1
**Track:** domain|api
**Cursor:** Implementer=generalPurpose; Reviewer=generalPurpose(review); Security=security-review on auth
**Depends on:** Task 2
**Files:**
- Create: `src/billing_platform/domain/models/api_key.py`, `src/billing_platform/services/organizations.py`, `src/billing_platform/api/v1/organizations.py`, `src/billing_platform/api/deps.py`
- Test: `tests/unit/test_api_key_hash.py`, `tests/integration/test_tenant_isolation.py`
**TZ:** §6.3 orgs/api_keys, §8.3, API A

**Goal:** CRUD org + issue API key; Bearer auth; all requests scoped by org; cross-tenant → 403.

**Interfaces:**
- Consumes: DualIdMixin, AsyncSession
- Produces:

```python
async def create_organization(session, *, name: str, external_id: str, idempotency_key: str) -> Organization
async def create_api_key(session, *, organization_id: int | None, role: str) -> tuple[ApiKey, str]  # raw once
async def authenticate(bearer: str) -> AuthContext  # organization_id, role, key_prefix
```

**Steps:**

- [x] **Failing test:**

```python
def test_api_key_hash_does_not_store_plaintext() -> None:
    from billing_platform.services.api_keys import hash_api_key, verify_api_key
    raw = "bp_test_secret_key_001"
    digest = hash_api_key(raw)
    assert raw not in digest
    assert verify_api_key(raw, digest) is True
```

- [x] **Run — FAIL.**

- [x] **Minimal implementation:** argon2/bcrypt hash; routes POST/GET/PATCH `/v1/organizations`, POST `/v1/organizations/{public_id}/api-keys`; dependency injects `AuthContext`; filter `Organization.id == ctx.organization_id` (except platform_admin).

- [x] **Integration FAIL→PASS:** org A key cannot GET org B → 403.

```bash
uv run pytest tests/unit/test_api_key_hash.py tests/integration/test_tenant_isolation.py -v
```

- [x] **Review Gates A–D** (C: secrets — raw key only in create response; logs — prefix) — **APPROVE** (+ security APPROVE; after fix: Idempotency-Key + PATCH platform_admin-only)
- [ ] **Commit** (on request): `feat: organizations, api keys, tenant isolation`

**Acceptance:** §10.2 cross-tenant 403; keys hashed.
**Risks:** sequential BIGINT in JSON response — Gate C fail.

---

### Task 4: catalog + publish

**Stage:** 1
**Track:** domain|api
**Cursor:** Implementer=generalPurpose; Reviewer=generalPurpose(review)
**Depends on:** Task 3
**Files:**
- Create: models `product/plan/price/feature/plan_feature`, `services/catalog.py`, `api/v1/catalog.py`
- Test: `tests/unit/test_plan_publish.py`, `tests/integration/test_catalog_snapshot.py`
**TZ:** §6.3 catalog, API B

**Goal:** Admin creates product/plan/prices/features; publish version; `GET /catalog/snapshot` reflects published.

**Interfaces:**

```python
async def publish_plan(session, plan_id: uuid.UUID) -> Plan  # sets published_at, bumps version rules
async def get_catalog_snapshot(session) -> CatalogSnapshot
```

**Steps:**

- [x] **Failing test:**

```python
import pytest
from billing_platform.services.catalog import publish_plan, PlanNotDraftError

@pytest.mark.asyncio
async def test_cannot_publish_already_published(session, published_plan):
    with pytest.raises(PlanNotDraftError):
        await publish_plan(session, published_plan.id)
```

- [x] **Run — FAIL.**
- [x] **Implement** CRUD + publish + snapshot (UUIDv7 PK everywhere in catalog; `plan_features` surrogate PK + UNIQUE(plan_id, feature_id)).
- [x] **PASS** unit+integration.
- [x] **Review Gates A–D** — **APPROVE** (advisory: more adversarial tests for published mutation / non-admin 403)
- [ ] **Commit** (on request): `feat: catalog publish and snapshot`

**Acceptance:** draft→publish; snapshot contains plan_features.
**Risks:** mutating published plan in-place — forbidden; new version.

---

### Task 5: subscriptions + state machine

**Stage:** 1
**Track:** domain|api
**Cursor:** Implementer=generalPurpose; Reviewer=generalPurpose(review)
**Depends on:** Task 4
**Files:**
- Create: `domain/models/subscription.py`, `domain/state_machines/subscription.py`, `services/subscriptions.py`, `api/v1/subscriptions.py`
- Test: `tests/unit/test_subscription_state_machine.py`
**TZ:** App. A, API C, §10.2 unit illegal transition

**Goal:** create/list/get/cancel; dual-id subscription; SM rejects `canceled→trialing`.

**Interfaces:**

```python
ALLOWED_TRANSITIONS: dict[SubscriptionStatus, set[SubscriptionStatus]]
def transition(current: SubscriptionStatus, new: SubscriptionStatus) -> SubscriptionStatus  # raises IllegalTransition
async def create_subscription(...) -> Subscription
async def cancel_subscription(..., at_period_end: bool) -> Subscription
```

**Steps:**

- [x] **Failing test:**

```python
import pytest
from billing_platform.domain.state_machines.subscription import transition, IllegalTransition
from billing_platform.domain.models.subscription import SubscriptionStatus

def test_canceled_to_trialing_illegal() -> None:
    with pytest.raises(IllegalTransition):
        transition(SubscriptionStatus.canceled, SubscriptionStatus.trialing)
```

- [x] **Run — FAIL.**
- [x] **Implement** SM + API; create → `trialing` or `incomplete` per plan.trial_days; idempotency_key UNIQUE.
- [x] **PASS.**
- [x] **Review Gates A–D** (D: retries create with same Idempotency-Key). — **APPROVE** (after mypy fix)
- [ ] **Commit** (on request): `feat: subscriptions and state machine`

**Acceptance:** §10.2 illegal transition covered; API C paths.
**Risks:** status sync only via webhook path (Task 7) for payment-driven transitions.

---

### Task 6: PaymentProviderPort + mock Stripe + webhook persist-first

**Stage:** 1
**Track:** payments|api
**Cursor:** Implementer=generalPurpose; Reviewer=generalPurpose(review); Grounding=Stripe webhook signature; Security=security-review
**Depends on:** Task 5, Task 1 mock-stripe stub, ADR-005
**Files:**
- Create: `integrations/payment_provider.py` (Protocol), `integrations/mock_stripe/client.py`, `integrations/mock_stripe/signature.py`, mock-stripe service app, `api/v1/webhooks.py` (persist only)
- Test: `tests/unit/test_webhook_signature.py`, `tests/integration/test_webhook_persist_first.py`
**TZ:** §5.1, §7.4, ADR-005, §8.4

**Goal:** Port without Stripe SDK in domain; mock-stripe sends signed webhooks; API `POST /v1/webhooks/mock-stripe` verify HMAC ±5min and INSERT webhook_events (ON CONFLICT DO NOTHING) **before** business processing.

**Interfaces:**

```python
class PaymentProviderPort(Protocol):
    async def create_customer(self, *, organization_public_id: str, email: str) -> str: ...
    async def create_subscription(self, *, customer_id: str, price_id: str, trial_days: int) -> str: ...
    async def cancel_subscription(self, *, external_subscription_id: str) -> None: ...

def verify_stripe_signature(payload: bytes, header: str, secret: str, tolerance_seconds: int = 300) -> None
async def persist_webhook(session, *, provider_event_id: str, event_type: str, payload: dict) -> WebhookEvent | None
```

**Steps:**

- [x] **Failing test:**

```python
import pytest
from billing_platform.integrations.mock_stripe.signature import verify_stripe_signature, InvalidWebhookSignature

def test_invalid_signature_rejected(raw_payload: bytes) -> None:
    with pytest.raises(InvalidWebhookSignature):
        verify_stripe_signature(raw_payload, "t=1,v1=deadbeef", secret="whsec_test", tolerance_seconds=300)
```

- [x] **Run — FAIL.**
- [x] **Docs-grounding:** Stripe-compatible webhook signature
  Sources: https://docs.stripe.com/webhooks/signatures

- [x] **Implement** HMAC-SHA256 `v1`; mock-stripe endpoints customers/subscriptions/invoices + emit webhook; billing-api persist-first route returns 200 on duplicate insert.
- [x] **PASS.**
- [x] **Review Gates A–D** (C: webhook secret from env; D: clock skew). — **APPROVE** (+ security APPROVE; compose secret default aligned)
- [ ] **Commit** (on request): `feat: payment port, mock stripe, persist-first webhooks`

**Acceptance:** signature verified; duplicate provider_event_id → 200 no second row.
**Risks:** domain imports stripe SDK — Gate A fail.

---

### Task 7: webhook processor + idempotency

**Stage:** 1
**Track:** payments|domain
**Cursor:** Implementer=generalPurpose; Reviewer=generalPurpose(review)
**Depends on:** Task 6 (persist), Task 5 (SM); uses Outbox/Ledger stubs hooks → full in 8/10
**Files:**
- Create: `services/webhook_processor.py`
- Test: `tests/unit/test_webhook_idempotency.py`, `tests/integration/test_invoice_paid_activates_subscription.py`
**TZ:** §6.4, §8.2, §10.2

**Goal:** Processing `invoice.paid` / `invoice.payment_failed` / subscription events in one TX: status transition + (later) ledger + outbox + cache bump; duplicate processing does not create a second outbox/ledger.

**Interfaces:**

```python
async def process_webhook(session, webhook_id: uuid.UUID) -> None
# invoice.paid → subscription.active; payment_failed → past_due
```

**Steps:**

- [x] **Failing test:**

```python
@pytest.mark.asyncio
async def test_duplicate_webhook_does_not_double_outbox(session, paid_webhook_factory):
    wh = await paid_webhook_factory()
    await process_webhook(session, wh.id)
    await process_webhook(session, wh.id)
    assert await count_outbox(session, event_type="subscription.activated") == 1
    assert await count_ledger(session, entry_type="invoice_paid") == 1
```

- [x] **Run — FAIL.**
- [x] **Implement** status machine transitions; mark webhook processed; skip if already processed.
- [x] **PASS** (may temporarily mock outbox/ledger counters until Tasks 8/10, then remove mocks).
- [x] **Review Gates A–D** (D: poison payload → failed + last_error, no crash loop).
- [ ] **Commit** (on request): `feat: idempotent webhook processor`

**Acceptance:** §10.2 duplicate webhook; happy path invoice.paid → active.
**Risks:** processing outside TX with outbox — Gate A fail.

---

### Task 8: outbox + relay → Kafka (≥5 event types)

**Stage:** 1
**Track:** events
**Cursor:** Implementer=generalPurpose; Reviewer=generalPurpose(review); Grounding=outbox SKIP LOCKED + Kafka at-least-once + Celery vs relay
**Depends on:** Task 2 outbox table, Task 7 hooks, ADR-001/002/004
**Files:**
- Create: `services/outbox.py`, `outbox_relay/__main__.py`, `outbox_relay/publisher.py`, `events/schemas/v1/envelope.py`
- Test: `tests/unit/test_outbox_enqueue.py`, `tests/integration/test_outbox_relay_kafka.py`
**TZ:** §4.3.3, §7.2–7.3, §11.1

**Goal:** `OutboxService.enqueue` in the same TX; relay `FOR UPDATE SKIP LOCKED`; publish ≥5 event types; envelope schema_version=1.

**Interfaces:**

```python
@dataclass(frozen=True)
class EventEnvelope:
    schema_version: int
    event_id: str
    event_type: str
    occurred_at: str
    organization_id: str  # public_id string in payload boundary
    correlation_id: str
    payload: dict

async def enqueue(session: AsyncSession, *, event_type: str, aggregate_type: str, aggregate_id: str, organization_id: int, partition_key: str, payload: dict, idempotency_key: str) -> int

async def poll_and_publish(batch_size: int = 100) -> int  # returns published count
```

**Minimum 5 types for DoD:**
`subscription.trial_started`, `subscription.activated`, `subscription.payment_failed`, `subscription.past_due`, `subscription.canceled` (+ `ledger.entry_posted` desirable).

**Steps:**

- [x] **Failing test:**

```python
@pytest.mark.asyncio
async def test_relay_publishes_envelope_to_kafka(kafka_consumer, session):
    await enqueue(session, event_type="subscription.activated", ...)
    await session.commit()
    n = await poll_and_publish()
    assert n == 1
    msg = await kafka_consumer.getone()
    body = json.loads(msg.value)
    assert body["schema_version"] == 1
    assert body["event_type"] == "subscription.activated"
```

- [x] **Run — FAIL.**
- [x] **Docs-grounding:** transactional outbox + SKIP LOCKED; Kafka producer at-least-once; Celery vs relay (§12.3)
  Sources: PostgreSQL `FOR UPDATE SKIP LOCKED` docs; aiokafka producer docs; spec §12.3.

- [x] **Implement** relay loop; attempts≥10 → dead letter; Kafka message key = outbox id.
- [x] **PASS.**
- [x] **Review Gates A–D** (A: no Celery publish; D: poison → DLQ).
- [ ] **Commit** (on request): `feat: transactional outbox and kafka relay`

**Acceptance:** ≥5 types observed on topics; at-least-once safe.
**Risks:** dual-write — forbidden.

---

### Task 9: entitlement evaluator + Redis cache + invalidation

**Stage:** 1
**Track:** entitlements
**Cursor:** Implementer=generalPurpose; Reviewer=generalPurpose(review); Grounding=Redis cache invalidation/stampede
**Depends on:** Task 4–5, Task 7 cache bump hook, ADR-003
**Files:**
- Create: `services/entitlements.py`, `integrations/redis_cache.py`, `api/v1/entitlements.py`
- Test: `tests/unit/test_evaluator_quota_deny.py`, `tests/unit/test_past_due_grace_degraded.py`, `tests/integration/test_evaluate_cache_hit.py`
**TZ:** §4.3.5, API D, §10.2, §11.1

**Goal:** `POST /v1/entitlements/evaluate` read-only; Redis hit → `cache_hit=true`; post-webhook invalidation < 60s; quota exhausted → deny; past_due+grace → degraded per policy.

**Interfaces:**

```python
@dataclass
class EvaluateResult:
    allowed: bool
    limit: int | None
    used: int | None
    remaining: int | None
    reason: str | None

@dataclass
class EvaluateResponse:
    organization_id: str
    subscription_status: str
    results: list[EvaluateResult]
    cache_hit: bool
    evaluated_at: datetime
    version: int

async def evaluate(session, redis, *, org_public_id: uuid.UUID, checks: list[Check]) -> EvaluateResponse
async def bump_entitlement_version(redis, *, organization_id: int) -> int
```

Cache key: `ent:org:{org_id}:v{version}`.

**Steps:**

- [x] **Failing tests:**

```python
def test_quota_exhausted_deny() -> None:
    decision = decide_feature(feature_type="quota", limit=10, used=10, enforcement="hard")
    assert decision.allowed is False

def test_past_due_in_grace_degraded() -> None:
    decision = decide_access(status="past_due", grace_active=True, enforcement="degraded")
    assert decision.mode == "degraded"
```

- [x] **Run — FAIL.**
- [x] **Docs-grounding:** Redis cache invalidation / stampede
  Sources: Redis SET/GET docs; singleflight/lock pattern notes in ADR-003.

- [x] **Implement** evaluator + API + invalidate admin endpoint; webhook processor calls bump.
- [x] **PASS** including second evaluate `cache_hit=true`.
- [x] **Review Gates A–D** (A: no Kafka read; D: stale cache after bump).
- [ ] **Commit** (on request): `feat: entitlement evaluator with redis cache`

**Acceptance:** §11.1 evaluate + cache_hit + invalidate < 60s.
**Risks:** evaluate writes usage — forbidden (usage separate path; S1 stub OK).

---

### Task 10: ledger minimal postings

**Stage:** 1
**Track:** ledger
**Cursor:** Implementer=generalPurpose; Reviewer=generalPurpose(review)
**Depends on:** Task 7–8, ADR-006
**Files:**
- Create: `domain/models/ledger.py`, `services/ledger.py`, `api/v1/ledger.py`
- Test: `tests/unit/test_ledger_reversal.py`, `tests/integration/test_ledger_on_activate.py`
**TZ:** §4.3.4, §6.3 ledger, API H, §10.2

**Goal:** INSERT-only postings on activate/pay; reversal creates new row; no UPDATE/DELETE in app code.

**Interfaces:**

```python
async def post(session, *, organization_id: int, entry_type: str, amount_cents: int, currency: str, idempotency_key: str, correlation_id: str, subscription_id: int | None = None, invoice_id: int | None = None) -> LedgerEntry

async def reverse(session, *, entry_id: int, idempotency_key: str, correlation_id: str) -> LedgerEntry
```

**Steps:**

- [x] **Failing test:**

```python
@pytest.mark.asyncio
async def test_reversal_does_not_delete_original(session, posted_entry):
    rev = await reverse(session, entry_id=posted_entry.id, idempotency_key="rev-1", correlation_id="c1")
    assert rev.reverses_entry_id == posted_entry.id
    assert await get_entry(session, posted_entry.id) is not None
```

- [x] **Run — FAIL.**
- [x] **Implement** + wire into webhook processor TX + outbox `ledger.entry_posted`.
- [x] **PASS.**
- [x] **Review Gates A–D** (A: append-only; DB role note in docs).
- [ ] **Commit** (on request): `feat: append-only ledger postings`

**Acceptance:** pay/activate → ledger row; reversal keeps original.
**Risks:** mutable amount update — Gate A fail.

**Human checkpoint #2:** after Task 10 / end of `PHASE_4`. Epic webhooks/outbox/entitlements/ledger closed (Tasks 6–10); stop before recon/ops/demo. Phase map: [`AGENTS.md` §10.2](../../AGENTS.md#102-stage-1--foundation-specmd-§33--§111).

---

### Task 11: reconciliation manual run + seeded discrepancy

**Stage:** 1
**Track:** domain|api
**Cursor:** Implementer=generalPurpose; Reviewer=generalPurpose(review)
**Depends on:** Task 10, ADR-007
**Files:**
- Create: `services/reconciliation.py`, `api/v1/admin/reconciliation.py`, `scripts/seed_recon_mismatch.py`
- Test: `tests/unit/test_recon_amount_mismatch.py`, `tests/integration/test_manual_recon_run.py`
**TZ:** ADR-007, API G, §10.2, §11.1

**Goal:** `POST /admin/reconciliation/run` compares platform invoices/ledger vs mock Stripe registry; seeded mismatch → discrepancy row; re-run does not mutate invoices/ledger.

**Interfaces:**

```python
async def run_reconciliation(session, *, run_type: Literal["manual"], idempotency_key: str) -> ReconciliationRun
```

**Steps:**

- [x] **Failing test:**

```python
def test_amount_mismatch_detected() -> None:
    d = compare_amounts(expected_cents=1000, actual_cents=900)
    assert d.kind == "amount_mismatch"
    assert d.delta_cents == 100
```

- [x] **Run — FAIL.**
- [x] **Implement** manual run + list discrepancies; seed script.
- [x] **PASS.**
- [x] **Review Gates A–D** (D: idempotent re-run).
- [x] **Update runbook stub** `docs/runbooks/reconciliation-mismatch.md` outline.
- [ ] **Commit** (on request): `feat: manual reconciliation with discrepancies`

**Acceptance:** seeded discrepancy visible via Admin API.
**Risks:** auto-fix mutating ledger — forbidden.

---

### Task 12: health/ready/live + graceful shutdown

**Stage:** 1
**Track:** infra|api
**Cursor:** Implementer=generalPurpose; Reviewer=generalPurpose(review); Grounding=FastAPI lifespan
**Depends on:** Task 1, integrations pg/redis/kafka
**Files:**
- Modify: `main.py`, `api/v1/health.py`
- Test: `tests/integration/test_ready_fails_without_db.py`
**TZ:** §8.6, §10.2, §11.1

**Goal:** `/health/live` without dependencies; `/health/ready` ping PG+Redis+Kafka; SIGTERM drains in-flight ≤ `SHUTDOWN_GRACE_SECONDS`.

**Interfaces:**

```python
async def check_ready(settings) -> ReadyStatus  # ok | degraded reasons
```

**Steps:**

- [x] **Failing test:** ready returns non-200 when DATABASE_URL pointing to closed port.
- [x] **Docs-grounding:** FastAPI lifespan + graceful shutdown (Sources: FastAPI lifespan docs; Uvicorn `--timeout-graceful-shutdown`).
- [x] **Implement** probes + lifespan shutdown.
- [x] **PASS.**
- [x] **Review Gates A–D.**
- [ ] **Commit** (on request): `feat: readiness probes and graceful shutdown`

**Acceptance:** ready fails without DB; live still 200.
**Risks:** ready tying to Kafka too strictly in local — document degraded mode flag if needed.

---

### Task 13: Makefile targets lint/type/unit/integration (local CI)

**Stage:** 1
**Track:** infra
**Cursor:** Implementer=shell|generalPurpose; Reviewer=generalPurpose(review)
**Depends on:** Tasks 0–12 tests exist
**Files:**
- Modify: `Makefile`
- Create: `.github/workflows/ci.yml` (skeleton; without mandatory remote run)
**TZ:** §10.3–10.4

**Goal:** Local commands “as CI”: ruff, mypy, unit≥80%, integration on compose.test.

**Steps:**

- [x] **Failing check:** `make test-unit` missing → error.
- [x] **Implement Makefile:**

```makefile
lint:
        uv run ruff check src tests && uv run ruff format --check src tests
typecheck:
        uv run mypy src
test-unit:
        uv run pytest tests/unit -q --cov=billing_platform/services --cov=billing_platform/domain --cov-fail-under=80
test-integration:
        docker compose -f deploy/compose/docker-compose.test.yml run --rm tests
test: lint typecheck test-unit test-integration
```

- [x] **Run `make lint typecheck test-unit`** — PASS.
- [x] **ci.yml** mirrors make targets.
- [x] **Review Gates A–D.**
- [ ] **Commit** (on request): `chore: makefile local ci targets`

**Acceptance:** `make test` reproduces gates §10.4 locally.
**Risks:** coverage on empty packages — grow with services.

---

### Task 14: thin demo_ui after API happy-path

**Stage:** 1
**Track:** ui
**Cursor:** Implementer=generalPurpose; Reviewer=generalPurpose(review)
**Depends on:** Tasks 3–12 API happy-path
**Files:**
- Create: `demo_ui/` Vite React TS; `deploy/docker/Dockerfile.demo-ui`
**TZ:** §14

**Goal:** Screens: org, subscription, entitlements evaluate result, webhook status; no billing logic on client.

**Steps:**

- [x] **Failing check:** demo_ui package missing.
- [x] **Scaffold** Vite+React+TS; pages calling Internal/Admin API with API key from env.
- [x] **Manual/e2e smoke:** open UI, see subscription status after webhook.
- [x] **Review Gates A–D** (C: no secrets baked in image).
- [ ] **Commit** (on request): `feat: thin demo ui`

**Acceptance:** §11.1 demo UI checklist.
**Risks:** business logic in UI — Gate A/B fail.

---

### Task 15: README demo < 15 min + runbooks stubs

**Stage:** 1
**Track:** docs
**Cursor:** Implementer=generalPurpose; Reviewer=generalPurpose(review); Skills=`verification-before-completion`
**Depends on:** Tasks 1–14
**Files:**
- Create/Modify: `README.md`, `docs/runbooks/outbox-lag.md`, `docs/runbooks/webhook-replay.md`, `docs/runbooks/reconciliation-mismatch.md`, `docs/runbooks/dunning-stuck.md` (S2 stub), `scripts/seed_catalog.py`
**TZ:** §11.1, §13.7, App. C

**Goal:** README: from clone to happy-path < 15 min; runbook stubs with symptoms→actions.

**Steps:**

- [x] **Write README** with commands: `uv sync`, compose up, alembic, seed, demo script §13.7.
- [x] **Write runbook stubs** (headers + checklist).
- [x] **Verification:** walk checklist App. C locally; record evidence in orchestrator response.
- [x] **Review Gates A–D.**
- [x] **Human checkpoint #3:** before declaring stage 1 Done.
- [ ] **Commit** (on request): `docs: readme demo path and runbook stubs`

**Acceptance:** DoD §11.1 complete; local evidence.
**Risks:** declare Done without verification-before-completion — forbidden.

---

## F. Roadmap stages 2–3 (epics, not micro-steps)

### Stage 2 (roadmap only)
- Usage ingest / hourly aggregates / period close
- Full invoicing + line items sync mock Stripe
- Full ledger entry types (usage_charge, proration, credit, …)
- Daily reconciliation cron + alerts
- Dunning campaigns/attempts/pause (`DUNNING_ENABLED=true`)
- Grace policy engine
- Plan change + proration stub
- Celery workers for batch jobs
- RANGE partitions on `usage_events`

### Stage 3 (roadmap only)
- Read replica for evaluate/reports
- Helm chart HA + relay leader election
- API key rotation, audit export, stronger rate limits
- Advanced feature types / load targets
- **No sharding** (ADR §12.13); criteria-only roadmap

---

## G. Test strategy and local commands “as CI”

| Level | Command | Threshold |
|---------|---------|-------|
| Lint | `make lint` | ruff 0 |
| Types | `make typecheck` | mypy strict 0 |
| Unit | `make test-unit` | coverage ≥ 80% services+domain |
| Integration | `make test-integration` | 100% pass on compose.test |
| E2E | compose + demo_ui smoke | happy path |

Mandatory §10.2 cases embedded in Tasks 5, 7, 9, 10, 11, 12.

---

## H. Risks / failure modes → runbook → task

| Failure mode | Runbook | Task |
|--------------|---------|------|
| Outbox lag / poison | `docs/runbooks/outbox-lag.md` | 8 |
| Webhook fail / replay | `docs/runbooks/webhook-replay.md` | 6–7 |
| Recon mismatch | `docs/runbooks/reconciliation-mismatch.md` | 11 |
| Stale entitlement cache | AGENTS + invalidate API | 9 |
| Dunning stuck | `docs/runbooks/dunning-stuck.md` (S2) | ADR-008 |

---

## I. Calendar 8–10 weeks + human checkpoints

| Weeks | Focus | Tasks |
|--------|--------|-------|
| 1 | Bootstrap, Compose, ADR, Alembic | 0–2 |
| 2–3 | Orgs, catalog, subscriptions | 3–5 |
| 4–5 | Payments, webhooks, outbox/relay | 6–8 |
| 6–7 | Entitlements, ledger, recon | 9–11 |
| 8 | Health, Makefile CI, demo_ui, README | 12–15 |
| 9–10 | Buffer: coverage, adversarial, DoD | — |

**Human checkpoints** (canon = [`AGENTS.md` §10.1](../../AGENTS.md#101-common-entry)):
1. After Task 0 (`PHASE_0`) — stop; then PHASE_1 per [`AGENTS.md` §10.2](../../AGENTS.md#102-stage-1--foundation-specmd-§33--§111).
2. After Task 10 / end of `PHASE_4` — stop; then PHASE_5 per §10.2.
3. Before declaring stage 1 Done (after Task 15 verification / end of `PHASE_5`).

Between regular tasks do not ask “continue?” — follow the plan until BLOCKED/ambiguity/checkpoint.

---

## J. Plan DoD (stage 1 execution)

Stage 1 Done only if §11.1 true locally:

- [x] `docker compose up` works; README happy path < 15 min
- [x] org + subscription → webhook → `active`
- [x] evaluate reflects published plan; 2nd call `cache_hit=true`; invalidate < 60s
- [x] duplicate webhook does not multiply outbox/ledger
- [x] relay publishes ≥ 5 event types
- [x] minimal ledger on pay/activate; manual recon + seeded discrepancy
- [x] Alembic up/down; secrets not in repo; live/ready; graceful shutdown documented
- [x] structlog correlation_id + organization_id; OTel locally visible
- [x] pytest gates; OpenAPI `/docs`; ADR outbox/Kafka/ledger
- [x] Demo UI: org, subscription, entitlements, webhook status

*(Re-verification Checkpoint #3 + DoD #8: [`docs/progress.md`](../progress.md) — READY FOR STAGE1 DONE; human “accept” may still be required for formal closure.)*

---

## Cursor orchestration (reminder)

| Role | Mechanism |
|------|----------|
| Orchestrator | parent session |
| Implementer | Task `generalPurpose` fresh per task |
| Reviewer | another `generalPurpose` (≠ Implementer) |
| Explore | Task `explore` |
| Shell | Task `shell` |
| Security | Task `security-review` on webhooks/keys/tenant |
| Grounding | generalPurpose + WebFetch official docs |

Stop-the-line: REQUEST CHANGES / failing tests / grounding failure → fix → re-review.

**Execution start:**

```text
Execute @docs/plans/2026-02-14-stage1-implementation-plan.md via superpowers:subagent-driven-development.
Work locally, without git push and without gh.
Commits — only if I explicitly ask.
Start with Task 0 (Bootstrap). Implementer ≠ Reviewer.
```
