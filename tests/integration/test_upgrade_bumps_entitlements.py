"""Integration: plan change bumps entitlement version and evaluate reflects new plan."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

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
from billing_platform.db.session import close_db_engine, reset_db_singletons
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
    """Yield a Redis URL from Testcontainers (skip when Docker unavailable)."""
    try:
        with RedisContainer(REDIS_IMAGE) as redis_container:
            host = redis_container.get_container_host_ip()
            port = redis_container.get_exposed_port(6379)
            yield f"redis://{host}:{port}/0"
    except _DOCKER_UNAVAILABLE_EXCEPTIONS as exc:
        pytest.skip(f"Docker unavailable for RedisContainer: {exc}")


@pytest_asyncio.fixture
async def plan_change_api_client(
    migrated_postgres_url: str,
    redis_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[AsyncClient]:
    """API client with Postgres + Redis wired."""
    get_settings.cache_clear()
    await close_redis_client()
    monkeypatch.setenv("REDIS_URL", redis_url)
    monkeypatch.setenv("DATABASE_URL", migrated_postgres_url)
    get_settings.cache_clear()
    reset_db_singletons()

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
    await close_db_engine()
    reset_db_singletons()
    await engine.dispose()


@pytest.mark.integration
async def test_upgrade_bumps_entitlements_and_reflects_new_features(
    db_session: AsyncSession,
    plan_change_api_client: AsyncClient,
) -> None:
    """After change-plan to higher tier, evaluate cache miss shows new feature limits."""
    org = await create_organization(
        db_session,
        name="Upgrade Ent Org",
        external_id=f"ext-up-ent-{uuid.uuid4().hex[:8]}",
        idempotency_key=f"idem-up-ent-{uuid.uuid4().hex[:8]}",
    )
    _, org_raw = await create_api_key(
        db_session,
        organization_id=org.id,
        role=ApiKeyRole.PRODUCT_SERVICE.value,
    )

    product = await create_product(
        db_session,
        key=f"up_ent_prod_{uuid.uuid4().hex[:6]}",
        name="Upgrade Ent Product",
    )
    feature = await create_feature(
        db_session,
        key="api_calls",
        feature_type="quota",
        default_limit=1000,
    )
    basic = await create_plan(
        db_session,
        product_id=product.id,
        key=f"basic_ent_{uuid.uuid4().hex[:6]}",
        billing_interval="month",
        trial_days=0,
    )
    pro = await create_plan(
        db_session,
        product_id=product.id,
        key=f"pro_ent_{uuid.uuid4().hex[:6]}",
        billing_interval="month",
        trial_days=0,
    )
    await set_plan_features(
        db_session,
        basic.id,
        [
            PlanFeatureInput(
                feature_id=feature.id,
                limit_value=100,
                is_enabled=True,
                enforcement_mode="hard",
            )
        ],
    )
    await set_plan_features(
        db_session,
        pro.id,
        [
            PlanFeatureInput(
                feature_id=feature.id,
                limit_value=500,
                is_enabled=True,
                enforcement_mode="hard",
            )
        ],
    )
    await publish_plan(db_session, basic.id)
    await publish_plan(db_session, pro.id)

    now = datetime.now(UTC)
    subscription = Subscription(
        organization_id=org.id,
        plan_id=basic.id,
        status=SubscriptionStatus.active.value,
        current_period_start=now,
        current_period_end=now + timedelta(days=30),
    )
    db_session.add(subscription)
    await db_session.commit()

    evaluate_payload = {
        "organization_public_id": str(org.public_id),
        "checks": [{"feature_key": "api_calls", "quantity": 1}],
    }
    headers = {"Authorization": f"Bearer {org_raw}"}

    first = await plan_change_api_client.post(
        "/v1/entitlements/evaluate",
        json=evaluate_payload,
        headers=headers,
    )
    assert first.status_code == 200
    first_body = first.json()
    assert first_body["cache_hit"] is False
    assert first_body["results"][0]["limit"] == 100
    version_before_change = first_body["version"]

    second = await plan_change_api_client.post(
        "/v1/entitlements/evaluate",
        json=evaluate_payload,
        headers=headers,
    )
    assert second.status_code == 200
    assert second.json()["cache_hit"] is True

    change_resp = await plan_change_api_client.post(
        f"/v1/subscriptions/{subscription.public_id}/change-plan",
        json={"new_plan_id": str(pro.id), "effective": "immediate"},
        headers={
            "Authorization": f"Bearer {org_raw}",
            "Idempotency-Key": f"idem-change-{uuid.uuid4().hex[:8]}",
        },
    )
    assert change_resp.status_code == 200
    assert change_resp.json()["plan_id"] == str(pro.id)

    third = await plan_change_api_client.post(
        "/v1/entitlements/evaluate",
        json=evaluate_payload,
        headers=headers,
    )
    assert third.status_code == 200
    third_body = third.json()
    assert third_body["cache_hit"] is False
    assert third_body["version"] > version_before_change
    assert third_body["results"][0]["limit"] == 500
