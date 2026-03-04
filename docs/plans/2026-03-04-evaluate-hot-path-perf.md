# Evaluate hot-path performance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the per-request bcrypt KDF from API-key auth, trim evaluate hot-path round-trips, and document measured laptop vs extrapolated stand capacity honestly.

**Architecture:** High-entropy API keys (`bp_` + CSPRNG) are stored as **SHA-256 hex** with a **unique** lookup index (FIPS 180-4 / Python `hashlib.sha256`; not a password KDF). Evaluate uses one org load, one Redis snapshot key, and a shared read session with auth. Password KDFs (bcrypt/argon2) remain for **human passwords only** if those appear later — never on the evaluate Bearer path.

**Tech Stack:** FastAPI 0.141, SQLAlchemy 2 async, Alembic, Redis 8 asyncio, PostgreSQL 16, Python 3.12 `hashlib`, pytest 8, k6/Locust for measurement.

## Global Constraints

- Product SoT: `spec.md` v3.2 + Accepted ADRs; this plan **amends** `api_keys.key_hash` hashing (Task 1).
- PostgreSQL = SoT for entitlements; Kafka is post-commit only; **evaluate does not read Kafka**.
- Dual-write forbidden; ledger append-only; dual-id: BIGINT never in API.
- Tenant isolation by `organization_id` except `platform_admin`.
- Docs: professional English; **no invented RPS**; laptop numbers labeled as such; stand numbers are **extrapolation**, not DoD proof.
- **No git commit / push / gh** unless the human later asks. Skip every “Commit” step.
- Implementer ≠ Reviewer. TDD on code tasks. Official library docs over memory.
- Greenfield: **no backward compatibility** with bcrypt rows; local DBs re-seed demo keys after migration.
- ADR-009: this is a **breaking demo/local** schema change (delete unreadable bcrypt hashes), not a dual-write expand for old app binaries.
- Subagent models: Cursor built-ins only (`composer-2.5` or `cursor-grok-4.6-xhigh`). No `*-fast`, no BYOK slugs.
- Do not declare Stage Done.

---

### Task 1: ADR-015 + spec/AGENTS canon (auth hash + NFR framing)

**Files:**
- Create: `docs/adr/015-api-key-sha256-lookup.md`
- Modify: `spec.md` (api_keys table ~877; authentication ~315; §8.1 table — **replace bcrypt wording now**; leave measured RPS cells as “pending Task 4 measurement” only if you must — prefer qualitative NFR split: **auth must not use a password KDF**; **do not invent new 10k proof**)
- Modify: `AGENTS.md` §2 invariant (API keys SHA-256 lookup) and §0.2 ADR row
- Modify: `docs/adr/003-entitlement-cache.md` — add a short **Amendment** (date 2026-03-04): snapshot key is `ent:org:{id}:snapshot` (version bump **deletes** that key); version key remains for `version` in the HTTP response. Task 3 implements this; the ADR text is the contract.

**Interfaces:**
- Consumes: audit conclusion (bcrypt.checkpw ~180–210 ms/request)
- Produces: Accepted ADR-015; spec `key_hash` = SHA-256 hex, unique; password KDF forbidden on API-key verify

- [ ] **Step 1: Write ADR-015** using `docs/adr/0000-template.md`

Required decision text (verbatim intent):

1. API keys are CSPRNG high-entropy secrets (`bp_` + `secrets.token_urlsafe(32)`).
2. Persist **only** `SHA-256(raw_utf8)` as lowercase hex (64 chars) in `api_keys.key_hash`. Unique index. Prefix remains display-only (non-unique).
3. Authenticate: `hash = SHA-256(bearer)` then `SELECT … WHERE key_hash = :hash AND revoked_at IS NULL` (one indexed row). No bcrypt/argon2 on this path.
4. `verify_api_key(raw, digest)` uses `hmac.compare_digest` against `hash_api_key(raw)` (Python 3.12 `hmac`).
5. Grounding: Python [hashlib SHA-256](https://docs.python.org/3.12/library/hashlib.html); [hmac.compare_digest](https://docs.python.org/3.12/library/hmac.html#hmac.compare_digest). Password KDFs are for low-entropy secrets (OWASP Password Storage); they are the wrong tool for 256-bit API keys and destroy §8.1 evaluate RPS.
6. Remove runtime dependency on `bcrypt` once Task 2 lands.
7. Forbidden: per-request password KDF; prefix-scan + N verifies; storing plaintext keys.

- [ ] **Step 2: Patch `spec.md`**

- `key_hash`: `CHAR(64)` unique, SHA-256 hex of the raw key; never bcrypt/argon2 for API keys.
- Authentication sentence: hash in DB is SHA-256 lookup, not a password KDF.
- §8.1: add a row or footnote: **Bearer verify is O(1) SHA-256 + unique index** (must stay microseconds). Cached evaluate p50/p99 targets assume that. **Do not** lower 10k/12k to laptop smoke in this task. Add one sentence: laptop Compose (1 API replica) is **capacity characterization**, not profile A DoD (`§10.5` / `§11.3` already say this — tighten if needed).
- §8.1.1 profile E: success = **ramping-arrival-rate until abortOnFail** (Grafana breakpoint), not “hold 30k constant”.

- [ ] **Step 3: Patch `AGENTS.md` §2 + §0.2** for ADR-015. Sync §0.3.

- [ ] **Step 4: ADR-003 amendment** (snapshot key contract for Task 3). Do not implement Redis yet.

- [ ] **Step 5: Do not commit.**

**Acceptance:** ADR-015 Accepted; spec/AGENTS/ADR-003 amendment consistent; no application code.

---

### Task 2: SHA-256 API key hash + authenticate

**Files:**
- Modify: `src/billing_platform/domain/models/api_key.py` (`key_hash` `String(64)`, unique)
- Create: `alembic/versions/20260216_0019_api_keys_sha256_lookup.py` (`down_revision = "20260216_0018"`)
- Modify: `src/billing_platform/services/api_keys.py`
- Modify: `pyproject.toml` — remove `bcrypt>=5.0.0`
- Modify: `tests/unit/test_api_key_hash.py`, `tests/unit/test_api_keys_service.py`, `tests/integration/test_api_key_rotation.py`, `src/billing_platform/bootstrap/demo_seed.py`
- Test: existing auth tests must pass; add unique-lookup test

**Interfaces:**
- Consumes: ADR-015
- Produces:

```python
def hash_api_key(raw: str) -> str:
    """Return SHA-256 hex digest of UTF-8 raw key (FIPS 180-4)."""

def verify_api_key(raw: str, digest: str) -> bool:
    """Constant-time compare of hash_api_key(raw) vs stored digest."""

async def authenticate(session: AsyncSession, bearer: str) -> AuthContext:
    """Lookup by unique key_hash; no candidate loop."""
```

`AuthContext` stays the same fields in this task (`organization_id`, `role`, `key_prefix`, `api_key_id`). Organization public_id join is Task 3.

- [ ] **Step 1: Failing tests** (TDD)

`tests/unit/test_api_key_hash.py`:

```python
import hashlib
import hmac

from billing_platform.services.api_keys import hash_api_key, verify_api_key


def test_api_key_hash_is_sha256_hex_not_bcrypt() -> None:
    raw = "bp_test_secret_key_001"
    digest = hash_api_key(raw)
    assert digest == hashlib.sha256(raw.encode("utf-8")).hexdigest()
    assert len(digest) == 64
    assert not digest.startswith("$2")
    assert raw not in digest
    assert verify_api_key(raw, digest) is True
    assert verify_api_key(raw + "x", digest) is False


def test_verify_api_key_uses_constant_time_compare_shape() -> None:
    raw = "bp_test_secret_key_001"
    digest = hash_api_key(raw)
    assert hmac.compare_digest(digest, hash_api_key(raw)) is True
```

`tests/unit/test_api_keys_service.py`: authenticate still works; assert a single matching row (no bcrypt prefix `$2`).

`tests/integration/test_api_key_rotation.py`: change “Only bcrypt hashes” to “Only SHA-256 hex is persisted; raw secret never stored”; `assert len(key.key_hash) == 64`; `assert not key.key_hash.startswith("$2")`.

- [ ] **Step 2: Run tests — expect FAIL** (still bcrypt or missing unique)

Run: `uv run pytest tests/unit/test_api_key_hash.py -q`

- [ ] **Step 3: Implement hash + authenticate + model + migration**

`hash_api_key` / `verify_api_key`:

```python
import hashlib
import hmac

def hash_api_key(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def verify_api_key(raw: str, digest: str) -> bool:
    return hmac.compare_digest(hash_api_key(raw), digest)
```

`authenticate`: compute `lookup = hash_api_key(bearer)`; `select(ApiKey).where(ApiKey.key_hash == lookup, ApiKey.revoked_at.is_(None))`; `scalar_one_or_none()`; if none or expired → `ValueError`; return `AuthContext`. **No for-loop over prefix candidates.**

Alembic `20260216_0019` `upgrade()`:

1. `op.execute(sa.text("DELETE FROM api_keys"))` — bcrypt rows cannot be verified.
2. Drop index `ix_api_keys_key_prefix` only if you recreate it; keep prefix index.
3. `op.alter_column("api_keys", "key_hash", type_=sa.String(length=64), existing_nullable=False)`
4. Unique index `uq_api_keys_key_hash` on `key_hash`.

ORM: `key_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)`.

Remove `import bcrypt` and pyproject bcrypt extra. `uv lock` after pyproject change (`uv lock`).

Demo seed: `verify_api_key` still works with SHA-256.

- [ ] **Step 4: GREEN** `uv run pytest tests/unit/test_api_key_hash.py tests/unit/test_api_keys_service.py tests/integration/test_api_key_rotation.py -q` plus `uv run ruff check src/billing_platform/services/api_keys.py`

- [ ] **Step 5: Do not commit.**

**Acceptance:** No bcrypt in `src/`; unique SHA-256 lookup; tests green for this slice.

---

### Task 3: Evaluate hot-path noise (org once, snapshot key, shared read session)

**Files:**
- Modify: `src/billing_platform/integrations/redis_cache.py`
- Modify: `src/billing_platform/services/entitlements.py` (`evaluate` takes `organization`, not a second public_id fetch)
- Modify: `src/billing_platform/api/v1/entitlements.py`
- Modify: `src/billing_platform/api/deps.py` (`get_read_session`; skip extra org SELECT when `AuthContext.organization_public_id` is set)
- Modify: `src/billing_platform/services/api_keys.py` (`AuthContext.organization_public_id: UUID | None`; authenticate outerjoin)
- Modify: tests that override only `get_session` — also override `get_read_session` with the same fixture (grep `dependency_overrides[get_session]`)
- Modify: `tests/integration/test_evaluate_cache_hit.py` if snapshot key tests need updates
- Test: unit tests for redis snapshot key delete-on-bump; evaluate does not call `get_organization_by_public_id` when org is passed

**Interfaces:**
- Consumes: Task 2 `authenticate` / `hash_api_key`
- Produces:

```python
def entitlement_snapshot_key(organization_id: int) -> str:
    return f"ent:org:{organization_id}:snapshot"

async def increment_entitlement_version(redis: Redis, *, organization_id: int) -> int:
    """INCR version key; DELETE snapshot key (ADR-003 amendment)."""

async def get_or_build_cached_snapshot(
    redis: Redis,
    *,
    organization_id: int,
    ttl_seconds: int,
    builder: Callable[[], Awaitable[dict[str, Any]]],
) -> tuple[dict[str, Any], bool]:
    """One GET of entitlement_snapshot_key; stampede lock unchanged."""

async def evaluate(
    session: AsyncSession,
    redis: Redis,
    *,
    organization: Organization,
    checks: list[Check],
) -> EvaluateResponse:
    """No second org SELECT. version from version key (GET) only as needed for the response after snapshot hit — prefer embedding version in snapshot JSON field `cache_version` set at SET time from get_entitlement_version, so a hit is one Redis GET."""
```

Hit path Redis: **one GET** of `ent:org:{id}:snapshot`. Snapshot JSON includes `cache_version: int` (alongside `subscription_status`, `grace_active`, `features`). `_evaluate_checks` ignores `cache_version`. Bump: `increment_entitlement_version` deletes snapshot key so the next GET misses.

`get_auth_context`: `Depends(get_read_session)` instead of `get_session` so FastAPI **caches one read session** with `post_evaluate` (FastAPI dependency cache per request). Write routes keep `Depends(get_session)` for mutations; they will use two sessions (read auth + write) — acceptable.

Authenticate: `outerjoin` Organization; set `organization_public_id`. `deps.py` binds logging from that field; **no second SELECT** when it is not None. Platform admin (`organization_id is None`) skips bind as today.

- [ ] **Step 1: Failing tests**

- Redis: after `set` snapshot, `get_or_build` returns hit; after `increment_entitlement_version`, next get is miss.
- Evaluate service: mock/spy that `get_organization_by_public_id` is not called inside `evaluate` when `organization` is passed (unit with existing db fixtures if easier).
- Integration cache hit still `cache_hit true` then false after invalidate.

- [ ] **Step 2: Implement.** Keep stampede lock keys. Remove `entitlement_cache_key(org, version)` versioned key **or** keep as unused — prefer delete the versioned key helper to avoid two schemes.

- [ ] **Step 3: Fix integration overrides** so ASGI tests still inject the Testcontainers session for auth (`get_read_session`).

- [ ] **Step 4: GREEN** focused tests + `uv run ruff check` on touched files. `make typecheck` if time.

- [ ] **Step 5: Do not commit.**

**Acceptance:** Evaluate cache hit does not SELECT org twice; Redis hit is one GET; auth uses read session; tests green.

---

### Task 4: Rebuild, verify, measure, honest docs

**Files:**
- Modify: `docs/perf/README.md`, `docs/perf/k6_ceiling.js` (ramping-arrival-rate + abortOnFail per Grafana breakpoint docs; keep smoke 15 RPS or raise smoke if measurement supports it)
- Modify: `spec.md` §8.1 / §8.1.1 — **fill laptop measured numbers from this task’s k6/Locust**; keep stand 10k/12k as **targets assuming SHA-256 auth + ≥3 replicas**, with an extrapolation sentence (linear in verify-capable workers, not a claim of a 12k run)
- Create: `docs/perf/2026-03-04-hot-path-perf.md` (commands, RPS, p50/p99, error rate, config: workers, pool, replicas)
- Modify: `AGENTS.md` §0.2 if new perf report
- Makefile/compose only if required to rebuild API image

**Interfaces:**
- Consumes: Tasks 2–3 in the working tree
- Produces: evidence file + spec/docs that match evidence

- [ ] **Step 1: Quality gates** (fresh): `make lint`, `make typecheck`, `make test-unit`, `make test-integration`. Record output in the report. Fix failures (this task owns breakage from 2–3).

- [ ] **Step 2: Rebuild API image and recreate `billing-api`** via the repo’s documented compose path (`make compose-core` then load overlay `make _load_perf_rate_limits` or equivalent Makefile targets). Do not remap ports. Wait for `/health/ready`.

- [ ] **Step 3: Load characterization (same overlay as the audit: 1 replica, 4 workers, pool 8+4, rate limit 0)**

1. Idle: `GET /health/ready` time; two evaluate POSTs (`cache_hit` false then true) — record ms (no secrets in the report).
2. k6 plateaus: 15, 40, 80, 150 RPS × ~20–25s `constant-arrival-rate` (Docker k6 on compose network, stdin script — `scripts/run_k6_docker.sh` pattern). Record achieved RPS, fail%, dropped, p50/p99.
3. Optional Locust `EvaluateUser` 16 and 40 users × 20s.
4. Stop at error-rate / drop storm; do not claim §8.1.1 12k.

- [ ] **Step 4: Write `docs/perf/2026-03-04-hot-path-perf.md`** with the table and config. Update `spec.md` §8.1 footnote: laptop overlay **measured** X RPS evaluate cache-hit p50 Y ms (date, 1 replica, 4 workers). Stand 10k remains a **target** if verify stays SHA-256 and topology scales; **forbidden** to say 12k was measured here.

- [ ] **Step 5: Align `docs/perf/k6_ceiling.js`** with Grafana `ramping-arrival-rate` + `abortOnFail` (breakpoint). Smoke profile stays safe for CI/laptop.

- [ ] **Step 6: Do not commit.**

**Acceptance:** Gates green; compose API rebuilt from this tree; load evidence file exists; spec/docs do not contradict the evidence.

---

### Task 5: Find laptop evaluate ceiling (hold vs breakpoint)

Task 4 stopped at **150 RPS by plan**, not at a found limit. Human asked to **find the ceiling**.

**Files:**
- Modify: `docs/perf/k6_ceiling.js` — add `K6_PROFILE=laptop` (Grafana breakpoint: `ramping-arrival-rate` + `abortOnFail`; laptop-sane VUs; do **not** use `full` 30k/1000 VUs on this overlay)
- Reuse: `docs/perf/k6_hotpath_plateau.js` for hold plateaus
- Modify: `docs/perf/2026-03-04-hot-path-perf.md`, `spec.md` §8.1 footnote, `docs/perf/README.md`, `AGENTS.md` if the headline numbers change
- Test: `tests/unit/test_load_grafana_helpers.py` if ceiling script contract needs a laptop profile assertion

**Method (Grafana breakpoint testing + hold plateaus):**

1. Recreate load overlay (`make _load_perf_rate_limits`). Same knobs as Task 4 (1 replica, 4 workers, pool 8+4, rate limit 0, OTEL off). Do not remap ports. Wait `/health/ready`. Rebuild image only if the running API is not this tree.
2. **Hold plateaus** (`constant-arrival-rate`, ~20–25 s, Docker stdin, `MAX_VUS` high enough that k6 is not the first limiter — start **400**, raise if `dropped_iterations` with fail=0 and p50 still ~6 ms): 200, 300, 400, 500, 700, 1000, 1500… **Stop** at first **break**: fail% > 0, dropped storm, or achieved RPS ≪ target. Last **hold** = target ≈ achieved, 0% fail, 0 dropped.
3. **Breakpoint** (`K6_PROFILE=laptop`): one `ramping-arrival-rate` from below the last hold toward ~2–3× that hold over ~2–4 min; `abortOnFail` on `http_req_failed` (`rate<0.05`, `delayAbortEval` ~15–20 s). Capture the **last progress-line iters/s before abort**, not the whole-test average `http_reqs`.
4. Classify limiter: HTTP 5xx / timeouts vs k6 `dropped_iterations` (VU starvation) vs latency blow-up vs CPU of `billing-api`. Recreate API if a saturating run wedges it.
5. **Forbidden:** `K6_PROFILE=full` on this laptop; inventing RPS; claiming 12k; treating whole-test `http_reqs` average as the abort RPS.

**Acceptance:** Evidence file names a **last hold** and a **break/abort** with overlay knobs; spec footnote matches; 10k/12k remain stand targets; no commit.

---

## Self-review (orchestrator)

- Spec coverage: auth hash, evaluate cache, NFR honesty, breakpoint E, laptop vs stand — all tasked.
- No bcrypt on hot path after Task 2.
- Types: `hash_api_key` → `str` hex 64; `evaluate(..., organization: Organization, checks: list[Check])`.
- Dual session fix depends on FastAPI caching `Depends(get_read_session)` — Task 3 tests must override it.
