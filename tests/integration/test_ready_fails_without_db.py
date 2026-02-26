"""Integration: /health/ready fails when PostgreSQL is unreachable; /health/live stays 200."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from billing_platform.config import get_settings
from billing_platform.db.session import close_db_engine, reset_db_singletons
from billing_platform.integrations.redis_cache import close_redis_client
from billing_platform.main import create_app


@pytest.mark.integration
async def test_ready_non_200_when_db_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Env DATABASE_URL wins over `.env` (pydantic-settings); engine must use that DSN."""
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://billing:billing@127.0.0.1:59999/billing",
    )
    get_settings.cache_clear()
    reset_db_singletons()
    try:
        with (
            patch(
                "billing_platform.api.v1.health._check_redis",
                new_callable=AsyncMock,
                return_value=(True, None),
            ),
            patch(
                "billing_platform.api.v1.health._check_kafka",
                new_callable=AsyncMock,
                return_value=(True, None),
            ),
        ):
            app = create_app()
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                live = await client.get("/health/live")
                ready = await client.get("/health/ready")
    finally:
        await close_redis_client()
        await close_db_engine()
        reset_db_singletons()
        get_settings.cache_clear()

    assert live.status_code == 200
    assert live.json()["status"] == "ok"
    assert ready.status_code == 503
    body = ready.json()
    assert body["status"] == "unavailable"
    assert body["checks"]["postgres"] == "fail"
