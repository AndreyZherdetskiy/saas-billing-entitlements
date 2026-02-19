"""Unit: evaluate L1 then Redis; bump drops this-process L1."""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from redis.asyncio import Redis

from billing_platform.integrations.redis_cache import increment_entitlement_version
from billing_platform.services.entitlements import Check, evaluate
from billing_platform.services.hotpath_cache import (
    get_l1_org,
    get_l1_snapshot,
    set_l1_org,
    set_l1_snapshot,
)


def _snapshot(*, version: int = 4) -> dict[str, Any]:
    return {
        "subscription_status": "active",
        "grace_active": False,
        "features": {},
        "cache_version": version,
    }


@pytest.mark.asyncio
async def test_evaluate_l1_hit_does_not_call_redis_get() -> None:
    org_id = 4242
    public_id = uuid.uuid4()
    set_l1_snapshot(org_id, _snapshot(version=8))
    redis = MagicMock()
    redis.get = AsyncMock(side_effect=AssertionError("Redis GET must not run on L1 hit"))

    response = await evaluate(
        redis,
        organization_id=org_id,
        organization_public_id=public_id,
        checks=[Check(feature_key="api_calls", quantity=1)],
        session=None,
    )

    redis.get.assert_not_called()
    assert response.cache_hit is True
    assert response.version == 8
    assert response.organization_public_id == str(public_id)
    assert response.subscription_status == "active"


@pytest.mark.asyncio
async def test_evaluate_l1_miss_uses_redis_then_fills_l1() -> None:
    org_id = 77
    public_id = uuid.uuid4()
    snapshot = _snapshot(version=3)

    async def fake_get_or_build(*_args: object, **_kwargs: object) -> tuple[dict[str, Any], bool]:
        return snapshot, True

    with patch(
        "billing_platform.services.entitlements.get_or_build_cached_snapshot",
        new=AsyncMock(side_effect=fake_get_or_build),
    ):
        response = await evaluate(
            MagicMock(),
            organization_id=org_id,
            organization_public_id=public_id,
            checks=[Check(feature_key="api_calls", quantity=1)],
            session=None,
        )

    assert response.cache_hit is True
    assert get_l1_snapshot(org_id) == snapshot
    assert response.version == 3


@pytest.mark.asyncio
async def test_increment_entitlement_version_drops_l1(redis_client: Redis) -> None:
    org_id = 9002
    public_id = uuid.uuid4()
    set_l1_snapshot(org_id, _snapshot(version=1))
    set_l1_org(public_id, org_id)
    await increment_entitlement_version(redis_client, organization_id=org_id)
    assert get_l1_snapshot(org_id) is None
    assert get_l1_org(public_id) is None
