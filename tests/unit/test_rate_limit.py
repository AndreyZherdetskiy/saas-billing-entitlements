"""Unit tests for Redis fixed-window API key rate limiting."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from redis.asyncio import Redis

from billing_platform.config import Settings
from billing_platform.domain.models.api_key import ApiKeyRole
from billing_platform.services.rate_limit import (
    WINDOW_SECONDS,
    check_rate_limit,
    rate_limit_window_key,
    resolve_api_rate_limit_per_minute,
)


@pytest.mark.asyncio
async def test_rate_limit_allows_up_to_limit(redis_client: Redis) -> None:
    api_key_id = uuid.uuid4()
    fixed_now = datetime(2026, 2, 17, 12, 34, tzinfo=UTC)
    limit = 3

    for expected_remaining in (2, 1, 0):
        decision = await check_rate_limit(
            redis_client,
            api_key_id=api_key_id,
            limit_per_minute=limit,
            now=fixed_now,
        )
        assert decision.allowed is True
        assert decision.remaining == expected_remaining
        assert decision.retry_after_seconds is None

    decision = await check_rate_limit(
        redis_client,
        api_key_id=api_key_id,
        limit_per_minute=limit,
        now=fixed_now,
    )
    assert decision.allowed is False
    assert decision.remaining == 0
    assert decision.retry_after_seconds is not None
    assert 1 <= decision.retry_after_seconds <= WINDOW_SECONDS


@pytest.mark.asyncio
async def test_rate_limit_window_key_format() -> None:
    api_key_id = uuid.UUID("018f1234-5678-7890-abcd-ef1234567890")
    moment = datetime(2026, 2, 17, 9, 7, tzinfo=UTC)
    assert rate_limit_window_key(api_key_id, now=moment) == (
        "rl:api_key:018f1234-5678-7890-abcd-ef1234567890:202602170907"
    )


def test_resolve_api_rate_limit_per_minute_by_role() -> None:
    settings = Settings(
        api_rate_limit_per_minute=120,
        api_rate_limit_platform_admin_per_minute=1000,
    )
    assert (
        resolve_api_rate_limit_per_minute(
            role=ApiKeyRole.PRODUCT_SERVICE.value,
            settings=settings,
        )
        == 120
    )
    assert (
        resolve_api_rate_limit_per_minute(
            role=ApiKeyRole.PLATFORM_ADMIN.value,
            settings=settings,
        )
        == 1000
    )
