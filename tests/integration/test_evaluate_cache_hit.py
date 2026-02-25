"""Integration: evaluate cache hit and invalidation after version bump."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import docker.errors
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis
from requests.exceptions import ConnectionError as RequestsConnectionError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.community.redis import RedisContainer

from billing_platform.config import get_settings
from billing_platform.db import get_read_session, get_session
from billing_platform.domain.models.api_key import ApiKey, ApiKeyRole
from billing_platform.domain.models.subscription import Subscription, SubscriptionStatus
from billing_platform.integrations.redis_cache import close_redis_client, get_redis_client
from billing_platform.main import create_app
from billing_platform.services.api_keys import create_api_key, revoke_api_key
from billing_platform.services.catalog import (
    PlanFeatureInput,
    create_feature,
    create_plan,
    create_product,
    publish_plan,
    set_plan_features,
)
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
async def evaluate_api_client(
    migrated_postgres_url: str,
    redis_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[AsyncClient]:
    """API client with Postgres + Redis wired."""
    get_settings.cache_clear()
    await close_redis_client()
    monkeypatch.setenv("REDIS_URL", redis_url)

    engine = create_async_engine(migrated_postgres_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async def override_get_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    async def override_get_redis() -> Redis:
        return await get_redis_client()

    from billing_platform.api.v1 import entitlements as entitlements_api

    app = create_app()
    app.dependency_overrides[get_session] = override_get_session
    app.dependency_overrides[get_read_session] = override_get_session
    app.dependency_overrides[entitlements_api.get_redis] = override_get_redis

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    app.dependency_overrides.clear()
    get_settings.cache_clear()
    await close_redis_client()
    await engine.dispose()


async def _seed_evaluate_org(
    db_session: AsyncSession,
) -> tuple[object, ApiKey, dict[str, object], dict[str, str], dict[str, str]]:
    org = await create_organization(
        db_session,
        name="Cache Hit Org",
        external_id=f"ext-cache-{uuid.uuid4().hex[:8]}",
        idempotency_key=f"idem-cache-{uuid.uuid4().hex[:8]}",
    )
    _, admin_raw = await create_api_key(
        db_session,
        organization_id=None,
        role=ApiKeyRole.PLATFORM_ADMIN.value,
    )
    org_key, org_raw = await create_api_key(
        db_session,
        organization_id=org.id,
        role=ApiKeyRole.PRODUCT_SERVICE.value,
    )

    product = await create_product(
        db_session,
        key=f"cache_prod_{uuid.uuid4().hex[:6]}",
        name="Cache Product",
    )
    feature = await create_feature(
        db_session,
        key="api_calls",
        feature_type="quota",
        default_limit=1000,
    )
    plan = await create_plan(
        db_session,
        product_id=product.id,
        key=f"cache_plan_{uuid.uuid4().hex[:6]}",
        billing_interval="month",
        trial_days=0,
    )
    await set_plan_features(
        db_session,
        plan.id,
        [
            PlanFeatureInput(
                feature_id=feature.id,
                limit_value=100,
                is_enabled=True,
                enforcement_mode="hard",
            )
        ],
    )
    await publish_plan(db_session, plan.id)

    now = datetime.now(UTC)
    subscription = Subscription(
        organization_id=org.id,
        plan_id=plan.id,
        status=SubscriptionStatus.active.value,
        current_period_start=now,
        current_period_end=now + timedelta(days=30),
    )
    db_session.add(subscription)
    await db_session.commit()

    payload: dict[str, object] = {
        "organization_public_id": str(org.public_id),
        "checks": [{"feature_key": "api_calls", "quantity": 1}],
    }
    headers = {"Authorization": f"Bearer {org_raw}"}
    admin_headers = {"Authorization": f"Bearer {admin_raw}"}
    return org, org_key, payload, headers, admin_headers


@pytest.mark.integration
async def test_evaluate_cache_hit_and_invalidate_bump(
    db_session: AsyncSession,
    evaluate_api_client: AsyncClient,
) -> None:
    """Second evaluate returns cache_hit=true; after invalidate, cache miss on new version."""
    _org, _org_key, payload, headers, admin_headers = await _seed_evaluate_org(db_session)

    first = await evaluate_api_client.post(
        "/v1/entitlements/evaluate",
        json=payload,
        headers=headers,
    )
    assert first.status_code == 200
    first_body = first.json()
    assert first_body["cache_hit"] is False
    assert first_body["subscription_status"] == "active"
    assert first_body["results"][0]["allowed"] is True
    assert first_body["results"][0]["limit"] == 100
    version_after_first = first_body["version"]

    second = await evaluate_api_client.post(
        "/v1/entitlements/evaluate",
        json=payload,
        headers=headers,
    )
    assert second.status_code == 200
    second_body = second.json()
    assert second_body["cache_hit"] is True
    assert second_body["version"] == version_after_first

    invalidate = await evaluate_api_client.post(
        "/v1/entitlements/invalidate",
        json={"organization_public_id": payload["organization_public_id"]},
        headers=admin_headers,
    )
    assert invalidate.status_code == 200
    bumped_version = invalidate.json()["version"]
    assert bumped_version > version_after_first

    third = await evaluate_api_client.post(
        "/v1/entitlements/evaluate",
        json=payload,
        headers=headers,
    )
    assert third.status_code == 200
    third_body = third.json()
    assert third_body["cache_hit"] is False
    assert third_body["version"] == bumped_version


@pytest.mark.integration
async def test_tenant_evaluate_skips_org_select(
    db_session: AsyncSession,
    evaluate_api_client: AsyncClient,
) -> None:
    """Matching tenant Bearer org skips organization SELECT on first evaluate."""
    _org, _org_key, payload, headers, _admin_headers = await _seed_evaluate_org(db_session)

    with patch(
        "billing_platform.api.v1.entitlements.get_organization_by_public_id",
        wraps=None,
    ) as org_spy:
        org_spy.return_value = None
        response = await evaluate_api_client.post(
            "/v1/entitlements/evaluate",
            json=payload,
            headers=headers,
        )

    assert response.status_code == 200
    org_spy.assert_not_called()


@pytest.mark.integration
async def test_platform_admin_evaluate_uses_org_l1_on_second_request(
    db_session: AsyncSession,
    evaluate_api_client: AsyncClient,
) -> None:
    """Admin evaluate fills org L1; the next admin evaluate does not SELECT org."""
    _org, _org_key, payload, _headers, admin_headers = await _seed_evaluate_org(db_session)

    first = await evaluate_api_client.post(
        "/v1/entitlements/evaluate",
        json=payload,
        headers=admin_headers,
    )
    assert first.status_code == 200

    with patch(
        "billing_platform.api.v1.entitlements.get_organization_by_public_id",
    ) as org_spy:
        second = await evaluate_api_client.post(
            "/v1/entitlements/evaluate",
            json=payload,
            headers=admin_headers,
        )

    assert second.status_code == 200
    assert second.json()["cache_hit"] is True
    org_spy.assert_not_called()


@pytest.mark.integration
async def test_revoke_in_process_evaluate_returns_401(
    db_session: AsyncSession,
    evaluate_api_client: AsyncClient,
) -> None:
    """Revoke drops auth L1 in this process; the next evaluate is 401."""
    org, org_key, payload, headers, _admin_headers = await _seed_evaluate_org(db_session)
    first = await evaluate_api_client.post(
        "/v1/entitlements/evaluate",
        json=payload,
        headers=headers,
    )
    assert first.status_code == 200

    await revoke_api_key(
        db_session,
        organization_id=org.id,
        actor_key_id=org_key.id,
    )
    await db_session.commit()

    second = await evaluate_api_client.post(
        "/v1/entitlements/evaluate",
        json=payload,
        headers=headers,
    )
    assert second.status_code == 401
