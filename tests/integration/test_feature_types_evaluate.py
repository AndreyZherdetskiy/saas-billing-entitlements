"""Integration: evaluate matrix for boolean/quota/rate_limit/seat feature types."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

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
from billing_platform.domain.models.api_key import ApiKeyRole
from billing_platform.domain.models.subscription import Subscription, SubscriptionStatus
from billing_platform.domain.models.usage_aggregate import UsageAggregate
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


@pytest_asyncio.fixture
async def feature_types_api_client(
    migrated_postgres_url: str,
    redis_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[AsyncClient]:
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


@pytest.mark.integration
async def test_evaluate_feature_type_matrix(
    db_session: AsyncSession,
    feature_types_api_client: AsyncClient,
) -> None:
    """End-to-end evaluate returns distinct semantics per feature_type."""
    org = await create_organization(
        db_session,
        name="Feature Types Org",
        external_id=f"ext-ft-{uuid.uuid4().hex[:8]}",
        idempotency_key=f"idem-ft-{uuid.uuid4().hex[:8]}",
    )
    _, org_raw = await create_api_key(
        db_session,
        organization_id=org.id,
        role=ApiKeyRole.PRODUCT_SERVICE.value,
    )

    product = await create_product(
        db_session,
        key=f"ft_prod_{uuid.uuid4().hex[:6]}",
        name="Feature Types Product",
    )
    boolean_feature = await create_feature(
        db_session,
        key="advanced_analytics",
        feature_type="boolean",
    )
    quota_feature = await create_feature(
        db_session,
        key="api_calls",
        feature_type="quota",
        default_limit=1000,
        reset_interval="month",
    )
    rate_feature = await create_feature(
        db_session,
        key="burst_api",
        feature_type="rate_limit",
        default_limit=50,
        reset_interval="hour",
    )
    seat_feature = await create_feature(
        db_session,
        key="seats",
        feature_type="seat",
        default_limit=5,
    )
    plan = await create_plan(
        db_session,
        product_id=product.id,
        key=f"ft_plan_{uuid.uuid4().hex[:6]}",
        billing_interval="month",
        trial_days=0,
    )
    await set_plan_features(
        db_session,
        plan.id,
        [
            PlanFeatureInput(
                feature_id=boolean_feature.id,
                limit_value=None,
                is_enabled=True,
                enforcement_mode="hard",
            ),
            PlanFeatureInput(
                feature_id=quota_feature.id,
                limit_value=100,
                is_enabled=True,
                enforcement_mode="hard",
            ),
            PlanFeatureInput(
                feature_id=rate_feature.id,
                limit_value=10,
                is_enabled=True,
                enforcement_mode="hard",
            ),
            PlanFeatureInput(
                feature_id=seat_feature.id,
                limit_value=5,
                is_enabled=True,
                enforcement_mode="hard",
            ),
        ],
    )
    await publish_plan(db_session, plan.id)

    now = datetime.now(UTC)
    hour_start = now.replace(minute=0, second=0, microsecond=0)
    subscription = Subscription(
        organization_id=org.id,
        plan_id=plan.id,
        status=SubscriptionStatus.active.value,
        current_period_start=now,
        current_period_end=now + timedelta(days=30),
        metadata_={"seat_quantity": 12},
    )
    db_session.add(subscription)
    await db_session.flush()

    db_session.add_all(
        [
            UsageAggregate(
                organization_id=org.id,
                feature_key="api_calls",
                hour_start=hour_start,
                quantity=Decimal(100),
            ),
            UsageAggregate(
                organization_id=org.id,
                feature_key="burst_api",
                hour_start=hour_start,
                quantity=Decimal(10),
            ),
            UsageAggregate(
                organization_id=org.id,
                feature_key="seats",
                hour_start=hour_start,
                quantity=Decimal(11),
            ),
        ]
    )
    await db_session.commit()

    headers = {"Authorization": f"Bearer {org_raw}"}
    payload = {
        "organization_public_id": str(org.public_id),
        "checks": [
            {"feature_key": "advanced_analytics"},
            {"feature_key": "api_calls"},
            {"feature_key": "burst_api"},
            {"feature_key": "seats", "quantity": 2},
        ],
    }
    response = await feature_types_api_client.post(
        "/v1/entitlements/evaluate",
        json=payload,
        headers=headers,
    )
    assert response.status_code == 200
    body = response.json()
    by_key = {item["feature_key"]: item for item in body["results"]}

    boolean_result = by_key["advanced_analytics"]
    assert boolean_result["feature_type"] == "boolean"
    assert boolean_result["allowed"] is True
    assert boolean_result["limit"] is None
    assert boolean_result["reason"] is None

    quota_result = by_key["api_calls"]
    assert quota_result["feature_type"] == "quota"
    assert quota_result["allowed"] is False
    assert quota_result["reason"] == "quota_exhausted"
    assert quota_result["used"] == 100

    rate_result = by_key["burst_api"]
    assert rate_result["feature_type"] == "rate_limit"
    assert rate_result["allowed"] is False
    assert rate_result["reason"] == "rate_limit_exhausted"
    assert rate_result["used"] == 10

    seat_result = by_key["seats"]
    assert seat_result["feature_type"] == "seat"
    assert seat_result["allowed"] is False
    assert seat_result["reason"] == "seat_exhausted"
    assert seat_result["limit"] == 12
    assert seat_result["used"] == 11
