"""Integration: tiered rate limits and webhook signature rejection (§11.3)."""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from unittest.mock import patch

import docker.errors
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from requests.exceptions import ConnectionError as RequestsConnectionError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.community.redis import RedisContainer

from billing_platform.config import get_settings
from billing_platform.db import get_read_session, get_session
from billing_platform.domain.models.api_key import ApiKeyRole
from billing_platform.integrations.redis_cache import close_redis_client
from billing_platform.main import create_app
from billing_platform.services.api_keys import create_api_key
from billing_platform.services.organizations import create_organization
from tests.docker_engine import REDIS_IMAGE

WEBHOOK_SECRET = "whsec_task43_integration"

_DOCKER_UNAVAILABLE_EXCEPTIONS = (
    docker.errors.DockerException,
    FileNotFoundError,
    ConnectionError,
    RequestsConnectionError,
)


@pytest_asyncio.fixture
async def redis_url() -> AsyncIterator[str]:
    """Yield a Redis URL from Testcontainers (skip when Docker unavailable)."""
    try:
        with RedisContainer(REDIS_IMAGE) as redis_container:
            host = redis_container.get_container_host_ip()
            port = redis_container.get_exposed_port(6379)
            yield f"redis://{host}:{port}/0"
    except _DOCKER_UNAVAILABLE_EXCEPTIONS as exc:
        pytest.skip(f"Docker unavailable for RedisContainer: {exc}")


@pytest_asyncio.fixture
async def tiered_rate_limit_client(
    migrated_postgres_url: str,
    redis_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[AsyncClient]:
    """API client with tiered per-minute limits: non-admin=2, platform_admin=5."""
    get_settings.cache_clear()
    await close_redis_client()
    monkeypatch.setenv("REDIS_URL", redis_url)
    monkeypatch.setenv("API_RATE_LIMIT_PER_MINUTE", "2")
    monkeypatch.setenv("API_RATE_LIMIT_PLATFORM_ADMIN_PER_MINUTE", "5")
    monkeypatch.setenv("MOCK_STRIPE_WEBHOOK_SECRET", WEBHOOK_SECRET)

    engine = create_async_engine(migrated_postgres_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async def override_get_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_session] = override_get_session
    app.dependency_overrides[get_read_session] = override_get_session

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    app.dependency_overrides.clear()
    get_settings.cache_clear()
    await close_redis_client()
    await engine.dispose()


@pytest.mark.integration
async def test_non_admin_hits_429_at_lower_tier_limit(
    db_session: AsyncSession,
    tiered_rate_limit_client: AsyncClient,
) -> None:
    """product_service keys use API_RATE_LIMIT_PER_MINUTE and block sooner than admin."""
    org = await create_organization(
        db_session,
        name="Stricter RL Org",
        external_id=f"ext-srl-{uuid.uuid4().hex[:8]}",
        idempotency_key=f"idem-srl-{uuid.uuid4().hex[:8]}",
    )
    _, raw_key = await create_api_key(
        db_session,
        organization_id=org.id,
        role=ApiKeyRole.PRODUCT_SERVICE.value,
    )
    await db_session.commit()

    settings = get_settings()
    assert settings.api_rate_limit_per_minute == 2
    assert settings.api_rate_limit_platform_admin_per_minute == 5

    headers = {"Authorization": f"Bearer {raw_key}"}
    fixed_now = datetime(2026, 2, 19, 10, 0, 0, tzinfo=UTC)
    with patch("billing_platform.services.rate_limit.datetime") as mock_datetime:
        mock_datetime.now.return_value = fixed_now
        mock_datetime.UTC = UTC
        for _ in range(2):
            response = await tiered_rate_limit_client.get(
                f"/v1/organizations/{org.public_id}",
                headers=headers,
            )
            assert response.status_code == 200

        blocked = await tiered_rate_limit_client.get(
            f"/v1/organizations/{org.public_id}",
            headers=headers,
        )
    assert blocked.status_code == 429
    assert blocked.json()["detail"] == "rate limit exceeded"


@pytest.mark.integration
async def test_platform_admin_uses_higher_tier_before_429(
    db_session: AsyncSession,
    tiered_rate_limit_client: AsyncClient,
) -> None:
    """platform_admin keys use API_RATE_LIMIT_PLATFORM_ADMIN_PER_MINUTE."""
    org = await create_organization(
        db_session,
        name="Admin RL Org",
        external_id=f"ext-arl-{uuid.uuid4().hex[:8]}",
        idempotency_key=f"idem-arl-{uuid.uuid4().hex[:8]}",
    )
    _, admin_raw = await create_api_key(
        db_session,
        organization_id=None,
        role=ApiKeyRole.PLATFORM_ADMIN.value,
    )
    await db_session.commit()

    admin_limit = get_settings().api_rate_limit_platform_admin_per_minute
    assert admin_limit == 5

    headers = {"Authorization": f"Bearer {admin_raw}"}
    fixed_now = datetime(2026, 2, 19, 10, 1, 0, tzinfo=UTC)
    with patch("billing_platform.services.rate_limit.datetime") as mock_datetime:
        mock_datetime.now.return_value = fixed_now
        mock_datetime.UTC = UTC
        for _ in range(admin_limit):
            response = await tiered_rate_limit_client.get(
                f"/v1/organizations/{org.public_id}",
                headers=headers,
            )
            assert response.status_code == 200

        blocked = await tiered_rate_limit_client.get(
            f"/v1/organizations/{org.public_id}",
            headers=headers,
        )
    assert blocked.status_code == 429


@pytest.mark.integration
async def test_webhook_invalid_signature_rejected_not_2xx(
    tiered_rate_limit_client: AsyncClient,
) -> None:
    """§11.3: invalid HMAC must be rejected with HTTP 4xx (existing contract: 400)."""
    raw = b'{"id":"evt_task43_bad","type":"invoice.paid"}'
    ts = int(time.time())
    response = await tiered_rate_limit_client.post(
        "/v1/webhooks/mock-stripe",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "Stripe-Signature": f"t={ts},v1=deadbeef",
        },
    )
    assert 400 <= response.status_code < 500
    assert response.status_code != 200
    assert "signature" in response.json()["detail"].lower()
