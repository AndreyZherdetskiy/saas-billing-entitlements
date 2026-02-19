"""Unit tests for /health/ready degraded mode when Kafka is optional (D4-002)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from billing_platform.api.v1.health import check_ready
from billing_platform.config import Settings
from billing_platform.main import create_app


@pytest.mark.asyncio
async def test_kafka_fail_with_health_kafka_optional_returns_degraded() -> None:
    settings = Settings(health_kafka_optional=True)

    with (
        patch(
            "billing_platform.api.v1.health._check_postgres",
            new_callable=AsyncMock,
            return_value=(True, None),
        ),
        patch(
            "billing_platform.api.v1.health._check_redis",
            new_callable=AsyncMock,
            return_value=(True, None),
        ),
        patch(
            "billing_platform.api.v1.health._check_kafka",
            new_callable=AsyncMock,
            return_value=(False, "kafka: connection refused"),
        ),
    ):
        result = await check_ready(settings)

    assert result.status == "degraded"
    assert result.checks == {"postgres": "ok", "redis": "ok", "kafka": "fail"}
    assert any("kafka" in reason for reason in result.reasons)


@pytest.mark.asyncio
async def test_ready_endpoint_kafka_optional_degraded_returns_200() -> None:
    settings = Settings(health_kafka_optional=True)

    with (
        patch("billing_platform.api.v1.health.get_settings", return_value=settings),
        patch(
            "billing_platform.api.v1.health._check_postgres",
            new_callable=AsyncMock,
            return_value=(True, None),
        ),
        patch(
            "billing_platform.api.v1.health._check_redis",
            new_callable=AsyncMock,
            return_value=(True, None),
        ),
        patch(
            "billing_platform.api.v1.health._check_kafka",
            new_callable=AsyncMock,
            return_value=(False, "kafka: connection refused"),
        ),
    ):
        app = create_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/health/ready")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded"
    assert body["checks"]["kafka"] == "fail"
