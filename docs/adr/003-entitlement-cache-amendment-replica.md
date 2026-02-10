# Amendment: ADR-003 Entitlement cache — read replica path

- **Status:** Accepted amendment (stage 3 plan; code — Tasks 36–37)
- **Date:** 2026-02-10
- **Base ADR:** [003-entitlement-cache.md](003-entitlement-cache.md)

## Amendment

1. Stage 3: on cache miss, evaluate **may** read snapshot/subscription/features from a **read replica** (`DATABASE_READ_URL`) if `replica_lag_seconds` < Settings threshold.
2. Otherwise — **fallback to primary**.
3. Entitlement version bump / invalidate writes — primary/Redis only, as today.
4. Usage write path is **not** moved to replica.

## Links

- Spec §8.1, §11.3; stage 3 plan Tasks 36–37; ADR-012
