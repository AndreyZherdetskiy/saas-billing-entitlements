# Evaluate hit-path (no PG/Redis on cache hit) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the evaluate cache-hit path process-local (auth + snapshot L1), skip leftover Postgres, re-measure the laptop overlay ceiling, and document **measured facts only**.

**Architecture:** In-process TTL caches in each Uvicorn worker: SHA-256 digest → `AuthContext` (invalidate by `api_key_id` on rotate/revoke); org id → entitlement snapshot dict (drop on version bump + TTL). Tenant evaluate matching `AuthContext.organization_public_id` does not `SELECT` the organization and does not open a DB session on L1 hit. Redis remains the cross-process snapshot SoT (ADR-003). PostgreSQL remains entitlements SoT on miss.

**Tech Stack:** FastAPI 0.141, SQLAlchemy 2 async, Redis 8 asyncio, Python 3.12 `time.monotonic` / `hashlib`, pytest 8, k6 via `scripts/run_k6_docker.sh`.

## Global Constraints

- Product SoT: `spec.md` + Accepted ADRs. **This plan may edit `spec.md` and `docs/`**. Design for a fast hit path, not for table RPS. Docs **state measured facts** (overlay knobs, RPS, p50/p99, fail%, dropped, limiter). Do not editorialize about 10k/12k, “missed NFR”, or “not a 12k run”.
- **Do not create new ADR files.** Fold hit-path cache into existing Accepted ADRs: **ADR-003** (snapshot L1 + skip org SELECT + no session on full hit) and **ADR-015** (auth L1). **Delete** `docs/adr/016-evaluate-hit-path-local-cache.md` if it exists. Rewrite ADR Decision sections to current truth; do not stack contradictory amendments.
- PostgreSQL = SoT for entitlements; Kafka post-commit only; **evaluate does not read Kafka**.
- Dual-write forbidden; ledger append-only; BIGINT never in API.
- Tenant isolation by `organization_id` except `platform_admin`.
- API keys: SHA-256 unique lookup (ADR-015). No password KDF on Bearer.
- Brief L1/auth-cache staleness across workers (TTL) is accepted; bump still deletes Redis snapshot **and** this-process L1.
- **No git commit / push / gh** unless the human asks.
- Implementer ≠ Reviewer. TDD on code. Official FastAPI 0.141 / Redis / Python 3.12 docs.
- Subagent models: Cursor built-ins only — `composer-2.5` (mechanical/docs) or `cursor-grok-4.6-xhigh` (code, load, reviews). No `*-fast`, no BYOK.
- Do not declare Stage Done.
- If you find another hit-path cost (session checkout, extra JSON parse, unused Depends), fix it in the same code task.

---

### Task 1: Canon — fold hit-path into ADR-003 + ADR-015; facts-only docs

**Files:**
- Delete: `docs/adr/016-evaluate-hit-path-local-cache.md` (must not remain; was a stacked ADR)
- Modify: `docs/adr/003-entitlement-cache.md` — **rewrite Decision** to current truth (do not leave Decision saying `ent:org:{id}:v{version}` while amendments contradict). Redis snapshot `ent:org:{id}:snapshot`; process L1; bump deletes Redis snapshot **and** this-process L1; skip org SELECT when tenant Bearer org matches body; no DB session on full tenant hit; miss → Redis then PostgreSQL. Keep replica details in the existing replica amendment file.
- Modify: `docs/adr/015-api-key-sha256-lookup.md` — **rewrite Decision** to include per-process auth L1 (`sha256_hex → AuthContext`, reverse `api_key_id → digest`, TTL 2s, invalidate on rotate/revoke). Postgres authenticate only on cache miss. SHA-256 unique lookup unchanged. Strip Context sermons about 10k RPS being unreachable.
- Modify: `spec.md` §4.3.5 / §8.1 footnote / changelog — L1 + auth cache cited as **ADR-003 / ADR-015**, never ADR-016. Delete “12k was not measured / ≠ 12k / not a 12k run” copy. Keep measured overlay numbers as facts (Task 3 overwrites after the new run).
- Modify: `AGENTS.md` §0.2 / §2 / hot-path notes — drop all ADR-016 links.
- Modify: `docs/perf/README.md` and `docs/perf/2026-03-04-hot-path-perf.md` — facts only; no 12k-apology; no “next increment ADR-016”.

**Interfaces:**
- Consumes: current SHA-256 auth + Redis snapshot
- Produces: **no ADR-016 file**; ADR-003 + ADR-015 are the living decisions

Decision intent (do not water down) — lives **inside** 003 and 015:

1. **Auth cache (015):** per-process map `sha256_hex → AuthContext` plus `api_key_id → sha256_hex`. TTL `AUTH_CACHE_TTL_SECONDS` default **2**. SHA-256 of bearer then memory; Postgres `SELECT` only on miss. Invalidate both indexes on rotate (old digest if known) and **always** on revoke by `api_key_id`. Re-check `expires_at` in memory if stored; otherwise TTL is the bound.
2. **Snapshot L1 (003):** per-process map `organization_id → snapshot dict`, TTL `ENTITLEMENT_L1_TTL_SECONDS` default **1**. Hit → no Redis GET. Miss → `ent:org:{id}:snapshot`. `increment_entitlement_version` deletes Redis snapshot **and** drops L1 for that org in this process. Other workers may serve L1 until TTL — accepted.
3. **Tenant evaluate (003):** if `ctx.organization_public_id == body.organization_public_id`, do not `SELECT` organization; use `ctx.organization_id` + public_id. `platform_admin` still loads org.
4. **No DB session on full hit (003):** `get_auth_context` must not `Depends(get_read_session)` for the cached path. Session only on auth miss / snapshot miss / admin org load. Tests: `request.app.dependency_overrides.get(get_read_session, get_read_session)`.
5. Forbidden: Kafka on evaluate; dual-write; caching raw API keys; logging bearer; unbounded L1 (TTL required); **new ADR files for this change**.

- [ ] **Step 1: Refactor ADR-003 and ADR-015; delete 016.**
- [ ] **Step 2: Patch spec / AGENTS / perf docs.** Grep the repo for `ADR-016` / `016-evaluate` — zero remaining references.
- [ ] **Step 3: Do not commit.**

**Acceptance:** File 016 gone; grep clean; ADR-003/015 Decision sections match the living hit path; no “failed 12k” copy; no application code.

---

### Task 2: Implement hit-path caches + skip org SELECT + lazy session

**Files:**
- Create: `src/billing_platform/services/hotpath_cache.py` (auth cache + snapshot L1; `clear_*` for tests)
- Modify: `src/billing_platform/config.py` — `auth_cache_ttl_seconds: int = 2`, `entitlement_l1_ttl_seconds: int = 1`
- Modify: `src/billing_platform/services/api_keys.py` — put/invalidate cache on authenticate success, rotate, revoke
- Modify: `src/billing_platform/api/deps.py` — `get_auth_context` without `Depends(get_read_session)`; cache first
- Modify: `src/billing_platform/api/v1/entitlements.py` — no unconditional `Depends(get_read_session)`; skip org SELECT for matching tenant
- Modify: `src/billing_platform/services/entitlements.py` — evaluate by `organization_id` + `public_id`; L1 then Redis; open session only to `_build_snapshot`
- Modify: `src/billing_platform/integrations/redis_cache.py` — bump drops L1
- Modify: `tests/conftest.py` — autouse clear hotpath caches
- Tests: unit for cache TTL/invalidate; evaluate tenant skip; L1 hit does not GET Redis; bump drops L1; integration evaluate still 200 cache_hit; rotation still 401 after revoke
- Optional: FastAPI 0.141 `ORJSONResponse` only if official docs support it and tests/OpenAPI still pass

**Interfaces:**

```python
def cache_auth_context(digest: str, ctx: AuthContext) -> None: ...
def get_cached_auth_context(digest: str) -> AuthContext | None: ...
def invalidate_auth_context(*, api_key_id: UUID) -> None: ...

def get_l1_snapshot(organization_id: int) -> dict | None: ...
def set_l1_snapshot(organization_id: int, snapshot: dict) -> None: ...
def drop_l1_snapshot(organization_id: int) -> None: ...
def clear_hotpath_caches() -> None: ...

async def evaluate(
    redis: Redis,
    *,
    organization_id: int,
    organization_public_id: UUID,
    checks: list[Check],
    session: AsyncSession | None = None,
) -> EvaluateResponse:
    """L1 then Redis. Build from Postgres only on miss (session required then)."""
```

Acquire session on miss:

```python
async def _acquire_read_session(request: Request):
    getter = request.app.dependency_overrides.get(get_read_session, get_read_session)
    async for session in getter():
        yield session
```

Use `time.monotonic` for TTL (Python 3.12).

- [ ] **Step 1: Failing tests** (cache miss/hit, revoke invalidates, L1 vs redis mock, tenant 403 still works, platform_admin still loads org).
- [ ] **Step 2: Implement.**
- [ ] **Step 3: GREEN** focused + `uv run ruff check` on touched files. `make typecheck` if time. Fix any other hit-path waste you confirm (do not wait).
- [ ] **Step 4: Do not commit.**

**Acceptance:** Tenant evaluate L1+auth hit: no Postgres, no Redis GET. Tests green. Autouse cache clear so tests do not leak.

---

### Task 3: Rebuild, measure ceiling, facts-only numbers

**Files:**
- Modify: `docs/perf/2026-03-04-hot-path-perf.md` (or dated follow-up if cleaner) — overlay knobs, hold, break, p50/p99, limiter. **No** 12k-scolding.
- Modify: `spec.md` §8.1 footnote + changelog with **this run’s** numbers as facts
- Modify: `AGENTS.md` pointers if headlines change

**Method:** same overlay as Task 5 (1 replica, 4 workers, pool 8+4, rate limit 0, OTEL off). `make compose-core` if image stale, then `make _load_perf_rate_limits`. Plateaus via `k6_hotpath_plateau.js` from last known hold upward (start ~300, then 400, 500, 700, 1000…) until break (`MAX_VUS` high enough). One `K6_PROFILE=laptop` breakpoint. Classify limiter. Recreate API if wedged.

- [x] **Step 1: Rebuild API image** so the container has Task 2 code.
- [x] **Step 2: Measure** hold vs break.
- [x] **Step 3: Write facts.** Do not commit.

**Acceptance:** Evidence matches spec footnote; secrets absent; no 12k editorializing.

---

## Self-review (orchestrator)

- Hit path: no PG, no Redis GET on tenant L1+auth hit.
- Revoke still 401 after TTL or immediate invalidate in this process; tests cover revoke in same process.
- Dual-id: still `public_id` in HTTP.
- Docs: measured facts only.
