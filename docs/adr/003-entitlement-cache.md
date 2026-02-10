# ADR-003: Entitlement cache strategy

- **Status:** Accepted
- **Date:** 2026-02-10
- **Spec:** §4.3.5, §12.4, §12.6

## Context

Evaluate is the hot path for the product gateway (stage 1 target 100 RPS; stage 3 — thousands). Every database miss is expensive; stale cache without invalidation → MRR leakage or false denials.

The original decision (2026-02-10) addressed snapshots as Redis key `ent:org:{org_id}:v{version}`. That key shape is **superseded** — evaluate snapshots use `ent:org:{id}:snapshot` with a separate version key (see Decision).

## Decision

1. **Redis snapshot (cross-process SoT on L1 miss):** key `ent:org:{id}:snapshot` (`{id}` = organization internal BIGINT). TTL **30–60 s** (`ENTITLEMENT_CACHE_TTL_SECONDS`, default 60). L1 miss → one Redis GET of that key.
2. **Version key:** `ent:org:{id}:version` — integer value is the HTTP evaluate response `version` field. Version bump **deletes** the snapshot key (do not address evaluate snapshots as `ent:org:{id}:v{version}`).
3. **Process-local snapshot L1 (per Uvicorn worker):** in-process map `organization_id → snapshot dict` with TTL `ENTITLEMENT_L1_TTL_SECONDS` (default **1**). L1 hit → no Redis GET. L1 miss → Redis GET (Decision 1). Use `time.monotonic()` for expiry.
4. **Invalidation:** bump version after webhook status change, override, plan publish / subscription change — do not rely on TTL alone. `increment_entitlement_version` **deletes** the Redis snapshot key **and** drops L1 for that org in **this** process. Other workers may serve stale L1 until TTL — accepted brief staleness.
5. **On miss (Redis or L1):** assemble snapshot from PostgreSQL (subscription + plan + plan_features + overrides + usage aggregates when present); write L1 + Redis on the miss path.
6. **Tenant evaluate:** when `ctx.organization_public_id == body.organization_public_id`, do **not** `SELECT organization`; use `ctx.organization_id` and `ctx.organization_public_id`. `platform_admin` still loads org from Postgres when required.
7. **No DB session on full hit:** tenant evaluate on auth L1 (ADR-015) + snapshot L1 + matching tenant → **no Postgres session**, **no Redis GET**, no unconditional org `SELECT`. Acquire `AsyncSession` only on auth miss, snapshot miss (Redis + Postgres build), or `platform_admin` org load. `get_auth_context` must **not** `Depends(get_read_session)` for the cached auth path. Tests: `request.app.dependency_overrides.get(get_read_session, get_read_session)`.
8. Evaluate is **read-only**; usage is written separately (`POST /usage/events`). API response includes `cache_hit` and reflects `subscription_status`.
9. **Forbidden:** read Kafka for authorize; mix evaluate and write usage in one call in stage 1; unbounded in-process caches without TTL; dual-write.

## Consequences

- Low latency on full hit (L1) and on Redis hit; brief staleness acceptable per product policy.
- Stampede control needed (singleflight / lock on miss) — implement in Task 9.
- Cross-worker L1 staleness until TTL or bump in-process — same product policy class as Redis TTL.
- Application modules for L1, deps, evaluate path — [`docs/plans/2026-02-26-evaluate-hit-path.md`](../plans/2026-02-26-evaluate-hit-path.md) Task 2.

## Alternatives considered

- PostgreSQL + PgBouncer only — simpler, worse latency/load at scale.
- Edge/CDN cache for entitlements — risky for security-sensitive authorize.
- Redis-only (no L1) — every hit still pays Redis RTT; measured overlay CPU-bound at hundreds of RPS.
- Keep unconditional org SELECT — redundant when tenant Bearer already binds org.

## Links

- ADR-002 (Kafka not for authorize), ADR-015 (auth L1 on Bearer path)
- Spec §4.3.5, §8.3, §12.7 (usage separate)
- Replica path: [003-entitlement-cache-amendment-replica.md](003-entitlement-cache-amendment-replica.md)
- Plan: [`docs/plans/2026-02-26-evaluate-hit-path.md`](../plans/2026-02-26-evaluate-hit-path.md)
