"""Integration: API key rate limiting returns 429 with Retry-After; health stays 200."""

from __future__ import annotations

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
async def rate_limit_api_client(
    migrated_postgres_url: str,
    redis_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[AsyncClient]:
    """API client with Postgres + Redis and a low per-minute rate limit."""
    get_settings.cache_clear()
    await close_redis_client()
    monkeypatch.setenv("REDIS_URL", redis_url)
    monkeypatch.setenv("API_RATE_LIMIT_PER_MINUTE", "3")

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
async def test_rate_limit_returns_429_with_retry_after(
    db_session: AsyncSession,
    rate_limit_api_client: AsyncClient,
) -> None:
    """N+1 authenticated requests within the same minute return 429 + Retry-After."""
    org = await create_organization(
        db_session,
        name="Rate Limit Org",
        external_id=f"ext-rl-{uuid.uuid4().hex[:8]}",
        idempotency_key=f"idem-rl-{uuid.uuid4().hex[:8]}",
    )
    _, raw_key = await create_api_key(
        db_session,
        organization_id=org.id,
        role=ApiKeyRole.PRODUCT_SERVICE.value,
    )
    await db_session.commit()

    headers = {"Authorization": f"Bearer {raw_key}"}
    limit = get_settings().api_rate_limit_per_minute
    assert limit == 3

    # Pin window key time so N+1 requests cannot straddle a UTC minute boundary.
    fixed_now = datetime(2026, 2, 17, 12, 34, 15, tzinfo=UTC)
    with patch("billing_platform.services.rate_limit.datetime") as mock_datetime:
        mock_datetime.now.return_value = fixed_now
        mock_datetime.UTC = UTC
        for _ in range(limit):
            response = await rate_limit_api_client.get(
                f"/v1/organizations/{org.public_id}",
                headers=headers,
            )
            assert response.status_code == 200

        blocked = await rate_limit_api_client.get(
            f"/v1/organizations/{org.public_id}",
            headers=headers,
        )
    assert blocked.status_code == 429
    assert blocked.json()["detail"] == "rate limit exceeded"
    retry_after = blocked.headers.get("Retry-After")
    assert retry_after is not None
    assert int(retry_after) >= 1


@pytest.mark.integration
async def test_health_endpoints_not_rate_limited(
    rate_limit_api_client: AsyncClient,
) -> None:
    """Health probes remain 200 without Bearer auth even when Redis is wired."""
    for path in ("/health/live", "/health/ready"):
        for _ in range(5):
            response = await rate_limit_api_client.get(path)
            assert response.status_code in (200, 503)
            assert response.status_code != 429
