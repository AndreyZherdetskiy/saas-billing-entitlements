"""Unit: entitlement snapshot key is one GET on hit; bump deletes it (ADR-003)."""

from __future__ import annotations

from typing import Any

import pytest
from redis.asyncio import Redis

from billing_platform.integrations import redis_cache as redis_cache_mod


def test_entitlement_snapshot_key_format() -> None:
    assert redis_cache_mod.entitlement_snapshot_key(42) == "ent:org:42:snapshot"


def _snapshot_body() -> dict[str, Any]:
    return {
        "subscription_status": "active",
        "grace_active": False,
        "features": {},
    }


@pytest.mark.asyncio
async def test_get_or_build_hit_then_miss_after_version_bump(redis_client: Redis) -> None:
    org_id = 9001
    builds = 0

    async def builder() -> dict[str, Any]:
        nonlocal builds
        builds += 1
        return _snapshot_body()

    first, first_hit = await redis_cache_mod.get_or_build_cached_snapshot(
        redis_client,
        organization_id=org_id,
        ttl_seconds=60,
        builder=builder,
    )
    assert first_hit is False
    assert builds == 1
    assert first["cache_version"] == 1
    assert await redis_client.get(redis_cache_mod.entitlement_snapshot_key(org_id)) is not None

    second, second_hit = await redis_cache_mod.get_or_build_cached_snapshot(
        redis_client,
        organization_id=org_id,
        ttl_seconds=60,
        builder=builder,
    )
    assert second_hit is True
    assert builds == 1
    assert second["cache_version"] == 1
    assert second["subscription_status"] == "active"

    bumped = await redis_cache_mod.increment_entitlement_version(
        redis_client, organization_id=org_id
    )
    assert bumped > 1
    assert await redis_client.get(redis_cache_mod.entitlement_snapshot_key(org_id)) is None

    third, third_hit = await redis_cache_mod.get_or_build_cached_snapshot(
        redis_client,
        organization_id=org_id,
        ttl_seconds=60,
        builder=builder,
    )
    assert third_hit is False
    assert builds == 2
    assert third["cache_version"] == bumped


@pytest.mark.asyncio
async def test_cache_hit_performs_one_redis_get(redis_client: Redis) -> None:
    org_id = 4242

    async def builder() -> dict[str, Any]:
        return _snapshot_body()

    await redis_cache_mod.get_or_build_cached_snapshot(
        redis_client,
        organization_id=org_id,
        ttl_seconds=60,
        builder=builder,
    )

    original_get = redis_client.get
    gets: list[object] = []

    async def counting_get(name: object) -> object:
        gets.append(name)
        return await original_get(name)

    redis_client.get = counting_get  # type: ignore[method-assign]

    snapshot, hit = await redis_cache_mod.get_or_build_cached_snapshot(
        redis_client,
        organization_id=org_id,
        ttl_seconds=60,
        builder=builder,
    )
    assert hit is True
    assert gets == [redis_cache_mod.entitlement_snapshot_key(org_id)]
    assert snapshot["cache_version"] == 1
    assert "cache_version" not in snapshot.get("features", {})
