"""Redis client and entitlement cache helpers (ADR-003)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

from redis.asyncio import Redis

from billing_platform.config import get_settings
from billing_platform.services.hotpath_cache import drop_l1_snapshot

_redis: Redis | None = None

BUILD_LOCK_TTL_SECONDS = 5
LOCK_WAIT_INTERVAL_SECONDS = 0.1
LOCK_WAIT_ATTEMPTS = 50


def entitlement_version_key(organization_id: int) -> str:
    """Redis key storing the current entitlement cache version for an org."""
    return f"ent:org:{organization_id}:version"


def entitlement_snapshot_key(organization_id: int) -> str:
    """Redis key for the org entitlement snapshot (ADR-003 amendment)."""
    return f"ent:org:{organization_id}:snapshot"


def entitlement_build_lock_key(organization_id: int) -> str:
    """Short-lived lock to prevent cache stampede on miss."""
    return f"ent:org:{organization_id}:build_lock"


async def get_redis_client() -> Redis:
    """Return a process-wide async Redis client (lazy singleton)."""
    global _redis
    if _redis is None:
        _redis = Redis.from_url(
            get_settings().redis_url,
            decode_responses=True,
        )
    return _redis


async def close_redis_client() -> None:
    """Close the shared Redis client (application shutdown)."""
    global _redis
    if _redis is not None:
        await _redis.aclose()
        _redis = None


async def get_entitlement_version(redis: Redis, *, organization_id: int) -> int:
    """Read current entitlement cache version (defaults to 1)."""
    raw = await redis.get(entitlement_version_key(organization_id))
    if raw is None:
        return 1
    return int(raw)


async def increment_entitlement_version(redis: Redis, *, organization_id: int) -> int:
    """INCR version key; DELETE snapshot key (ADR-003 amendment)."""
    version_key = entitlement_version_key(organization_id)
    # Baseline logical version is 1 when the key is absent; INCR alone would yield 1.
    await redis.setnx(version_key, "1")
    new_version = await redis.incr(version_key)
    await redis.delete(entitlement_snapshot_key(organization_id))
    drop_l1_snapshot(organization_id)
    return int(new_version)


async def get_cached_value(redis: Redis, key: str) -> dict[str, Any] | None:
    """Deserialize a JSON object from Redis, or None on miss."""
    raw = await redis.get(key)
    if raw is None:
        return None
    loaded = json.loads(raw)
    if not isinstance(loaded, dict):
        return None
    return loaded


async def set_cached_value(
    redis: Redis,
    key: str,
    value: dict[str, Any],
    *,
    ttl_seconds: int,
) -> None:
    """Store a JSON snapshot with TTL (Redis SET with EX)."""
    await redis.set(key, json.dumps(value), ex=ttl_seconds)


async def _stamp_cache_version(
    redis: Redis,
    *,
    organization_id: int,
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    snapshot["cache_version"] = await get_entitlement_version(
        redis, organization_id=organization_id
    )
    return snapshot


async def get_or_build_cached_snapshot(
    redis: Redis,
    *,
    organization_id: int,
    ttl_seconds: int,
    builder: Callable[[], Awaitable[dict[str, Any]]],
) -> tuple[dict[str, Any], bool]:
    """One GET of entitlement_snapshot_key; stampede lock unchanged."""
    cache_key = entitlement_snapshot_key(organization_id)

    cached = await get_cached_value(redis, cache_key)
    if cached is not None:
        return cached, True

    lock_key = entitlement_build_lock_key(organization_id)
    acquired = await redis.set(lock_key, "1", nx=True, ex=BUILD_LOCK_TTL_SECONDS)

    if acquired:
        try:
            cached = await get_cached_value(redis, cache_key)
            if cached is not None:
                return cached, True
            snapshot = await _stamp_cache_version(
                redis,
                organization_id=organization_id,
                snapshot=await builder(),
            )
            await set_cached_value(redis, cache_key, snapshot, ttl_seconds=ttl_seconds)
            return snapshot, False
        finally:
            await redis.delete(lock_key)

    for _ in range(LOCK_WAIT_ATTEMPTS):
        await asyncio.sleep(LOCK_WAIT_INTERVAL_SECONDS)
        cached = await get_cached_value(redis, cache_key)
        if cached is not None:
            return cached, True

    snapshot = await _stamp_cache_version(
        redis,
        organization_id=organization_id,
        snapshot=await builder(),
    )
    return snapshot, False
