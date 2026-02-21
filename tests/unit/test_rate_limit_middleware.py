"""Unit tests: rate-limit middleware fail-closed on Redis outage."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from redis.exceptions import RedisError

from billing_platform.middleware.rate_limit import enforce_rate_limit_for_api_key


@pytest.mark.asyncio
async def test_redis_error_fails_closed_with_503() -> None:
    with (
        patch(
            "billing_platform.middleware.rate_limit.get_redis_client",
            new=AsyncMock(side_effect=RedisError("connection refused")),
        ),
        pytest.raises(HTTPException) as exc_info,
    ):
        await enforce_rate_limit_for_api_key(
            api_key_id="018f1234-5678-7890-abcd-ef1234567890",
            limit_per_minute=120,
        )

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "rate limiting temporarily unavailable"


@pytest.mark.asyncio
async def test_disabled_limit_skips_redis_even_when_unavailable() -> None:
    with patch(
        "billing_platform.middleware.rate_limit.get_redis_client",
        new=AsyncMock(side_effect=RedisError("connection refused")),
    ):
        headers = await enforce_rate_limit_for_api_key(
            api_key_id="018f1234-5678-7890-abcd-ef1234567890",
            limit_per_minute=0,
        )

    assert headers is None
