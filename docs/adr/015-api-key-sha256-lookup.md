# ADR-015: API-key SHA-256 unique lookup

- **Status:** Accepted
- **Date:** 2026-03-04
- **Spec:** §2.2, §6.3 (`api_keys`), §8.1, §8.4, §12.10

## Context

Evaluate sits behind `Authorization: Bearer <api_key>`. The Stage 1 implementation stored `api_keys.key_hash` with bcrypt (cost 12) and verified with `bcrypt.checkpw` on every authenticated request. A 2026-03-04 hot-path audit measured **~180–210 ms per request** on that KDF — enough to cap a 4-worker replica near ~15–19 RPS.

API keys here are not human passwords. They are CSPRNG high-entropy secrets. Password KDFs (bcrypt, argon2, scrypt, PBKDF2) exist to slow guesses against **low-entropy** secrets ([OWASP Password Storage](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html)). Applying them to 256-bit API keys destroys evaluate throughput without a corresponding threat-model gain.

Greenfield / local-demo: no dual-hash compatibility with existing bcrypt rows. Task 2 deletes unreadable hashes and re-seeds demo keys (see Consequences vs ADR-009).

## Decision

1. API keys are CSPRNG high-entropy secrets (`bp_` + `secrets.token_urlsafe(32)`).
2. Persist **only** `SHA-256(raw_utf8)` as lowercase hex (64 chars) in `api_keys.key_hash`. Unique index. Prefix remains display-only (non-unique).
3. Authenticate: `hash = SHA-256(bearer)` then `SELECT … WHERE key_hash = :hash AND revoked_at IS NULL` (one indexed row). No bcrypt/argon2 on this path.
4. `verify_api_key(raw, digest)` uses `hmac.compare_digest` against `hash_api_key(raw)` (Python 3.12 `hmac`).
5. **Auth cache (per Uvicorn worker):** in-process map `sha256_hex → AuthContext` plus reverse index `api_key_id → sha256_hex`. TTL from settings `AUTH_CACHE_TTL_SECONDS` (default **2**). Lookup: SHA-256 of Bearer, then memory; Postgres authenticate (Decision 3) **only on miss**. On rotate, invalidate both indexes for the old digest when known; on revoke, **always** invalidate by `api_key_id`. If `expires_at` is stored on the entry, re-check in memory; otherwise TTL is the staleness bound. Use `time.monotonic()` for expiry.
6. Grounding: Python [hashlib SHA-256](https://docs.python.org/3.12/library/hashlib.html) (FIPS 180-4; `sha256(…).hexdigest()` is lowercase hex); [hmac.compare_digest](https://docs.python.org/3.12/library/hmac.html#hmac.compare_digest). Password KDFs are for low-entropy secrets (OWASP Password Storage); forbidden on the evaluate Bearer path.
7. Remove runtime dependency on `bcrypt` once Task 2 lands.
8. **Forbidden:** per-request password KDF; prefix-scan + N verifies; storing plaintext keys; caching raw API keys or Bearer tokens; log Bearer; unbounded in-process auth cache without TTL.

Password KDFs remain allowed later **only** for human passwords if those appear — never on the evaluate Bearer path.

## Consequences

- Bearer verify is O(1) SHA-256 + unique-index lookup (microseconds) on cache miss; auth L1 avoids Postgres on hit.
- Brief auth staleness across workers until TTL or explicit invalidate (revoke/rotate in-process) — accepted.
- Compromised DB still does not yield raw keys; SHA-256 of a 256-bit CSPRNG secret is not a practical online-guessing target.
- **vs ADR-009:** deleting bcrypt rows and altering `key_hash` to a 64-character digest is a **breaking demo/local** schema change, not a dual-write expand for old app binaries. Production ZDT expand/contract remains the rule for other tables. Local DBs re-seed demo keys after the Task 2 migration.
- Application code for auth cache — [`docs/plans/2026-02-26-evaluate-hit-path.md`](../plans/2026-02-26-evaluate-hit-path.md) Task 2.
- Snapshot L1, skip org SELECT, no session on full hit — ADR-003.

## Alternatives considered

- **Keep bcrypt / switch to argon2 on API keys** — rejected: correct for passwords, wrong for CSPRNG API keys; measured ~200 ms/request on bcrypt path.
- **HMAC-SHA256 with a server-side pepper** — deferred: extra secret to rotate; not required to restore evaluate RPS; can be an amendment if a pepper becomes a product requirement.
- **Prefix-scan then N `checkpw`** — rejected: still a password KDF per candidate; not O(1).
- **Store plaintext or reversible encryption** — rejected: spec §8.4 (SHA-256 hex; logs only prefix).
- **Shared external cache for auth (Redis)** — rejected for stage: extra RTT and secret-adjacent data in a shared store; in-process digest → `AuthContext` is sufficient with TTL + invalidate on rotate/revoke.

## Links

- Spec §2.2, §6.3, §8.1, §8.1.1, §8.4, §12.10
- ADR-003 (evaluate cache; snapshot L1 + tenant hot path), ADR-009 (ZDT; this change is the documented demo/local exception)
- Plan: [`docs/plans/2026-02-26-evaluate-hit-path.md`](../plans/2026-02-26-evaluate-hit-path.md)
- Python 3.12 [hashlib](https://docs.python.org/3.12/library/hashlib.html), [hmac.compare_digest](https://docs.python.org/3.12/library/hmac.html#hmac.compare_digest)
- [OWASP Password Storage Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html)
