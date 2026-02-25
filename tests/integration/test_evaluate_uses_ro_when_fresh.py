"""Integration: evaluate cache-miss uses RO session when replica lag is fresh."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import docker.errors
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from requests.exceptions import ConnectionError as RequestsConnectionError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.community.redis import RedisContainer

from billing_platform.config import get_settings
from billing_platform.db.replica import reset_replica_lag_provider, set_replica_lag_provider
from billing_platform.db.session import reset_db_singletons
from billing_platform.domain.models.api_key import ApiKeyRole
from billing_platform.domain.models.subscription import Subscription, SubscriptionStatus
from billing_platform.integrations.redis_cache import close_redis_client, get_redis_client
from billing_platform.main import create_app
from billing_platform.services.api_keys import create_api_key
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
    try:
        with RedisContainer(REDIS_IMAGE) as redis_container:
            host = redis_container.get_container_host_ip()
            port = redis_container.get_exposed_port(6379)
            yield f"redis://{host}:{port}/0"
    except _DOCKER_UNAVAILABLE_EXCEPTIONS as exc:
        pytest.skip(f"Docker unavailable for RedisContainer: {exc}")


@pytest.mark.integration
async def test_evaluate_uses_read_session_factory_when_lag_fresh(
    db_session: AsyncSession,
    migrated_postgres_url: str,
    redis_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cache-miss evaluate routes through get_read_session; fresh lag selects RO factory."""
    get_settings.cache_clear()
    reset_db_singletons()
    reset_replica_lag_provider()
    await close_redis_client()
    monkeypatch.setenv("REDIS_URL", redis_url)
    monkeypatch.setenv("DATABASE_URL", migrated_postgres_url)
    monkeypatch.setenv("DATABASE_READ_URL", migrated_postgres_url)
    get_settings.cache_clear()

    org = await create_organization(
        db_session,
        name="RO Evaluate Org",
        external_id=f"ext-ro-{uuid.uuid4().hex[:8]}",
        idempotency_key=f"idem-ro-{uuid.uuid4().hex[:8]}",
    )
    _, org_raw = await create_api_key(
        db_session,
        organization_id=org.id,
        role=ApiKeyRole.PRODUCT_SERVICE.value,
    )

    product = await create_product(
        db_session,
        key=f"ro_prod_{uuid.uuid4().hex[:6]}",
        name="RO Product",
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
        key=f"ro_plan_{uuid.uuid4().hex[:6]}",
        billing_interval="month",
        trial_days=0,
    )
    await set_plan_features(
        db_session,
        plan.id,
        [
            PlanFeatureInput(
                feature_id=feature.id,
                limit_value=50,
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

    primary_engine = create_async_engine(migrated_postgres_url, pool_pre_ping=True)
    primary_factory = async_sessionmaker(
        primary_engine, expire_on_commit=False, class_=AsyncSession
    )
    read_engine = create_async_engine(migrated_postgres_url, pool_pre_ping=True)
    read_factory = async_sessionmaker(read_engine, expire_on_commit=False, class_=AsyncSession)
    read_factory_spy = MagicMock(wraps=read_factory)

    async def lag_fresh() -> float:
        return 1.0

    set_replica_lag_provider(lag_fresh)

    from billing_platform.api.v1 import entitlements as entitlements_api
    from billing_platform.db import get_session

    async def override_get_session() -> AsyncIterator[AsyncSession]:
        async with primary_factory() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_session] = override_get_session
    app.dependency_overrides[entitlements_api.get_redis] = get_redis_client

    payload = {
        "organization_public_id": str(org.public_id),
        "checks": [{"feature_key": "api_calls", "quantity": 1}],
    }
    headers = {"Authorization": f"Bearer {org_raw}"}

    with patch(
        "billing_platform.db.session.get_read_session_factory",
        return_value=read_factory_spy,
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/v1/entitlements/evaluate",
                json=payload,
                headers=headers,
            )

    assert response.status_code == 200
    body = response.json()
    assert body["cache_hit"] is False
    assert body["results"][0]["allowed"] is True
    assert body["results"][0]["limit"] == 50
    read_factory_spy.assert_called()

    app.dependency_overrides.clear()
    get_settings.cache_clear()
    reset_replica_lag_provider()
    reset_db_singletons()
    await close_redis_client()
    await primary_engine.dispose()
    await read_engine.dispose()
