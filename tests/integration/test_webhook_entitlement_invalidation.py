"""Integration: webhook subscription change invalidates entitlement cache (§11.1)."""

from __future__ import annotations

import json
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
from billing_platform.domain.models.api_key import ApiKeyRole
from billing_platform.domain.models.subscription import Subscription, SubscriptionStatus
from billing_platform.integrations.mock_stripe.signature import sign_stripe_payload
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

WEBHOOK_SECRET = "whsec_test_webhook_invalidation"

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
async def webhook_evaluate_client(
    migrated_postgres_url: str,
    redis_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[AsyncClient]:
    """API client with Postgres, Redis, and webhook secret wired."""
    get_settings.cache_clear()
    await close_redis_client()
    monkeypatch.setenv("REDIS_URL", redis_url)
    monkeypatch.setenv("MOCK_STRIPE_WEBHOOK_SECRET", WEBHOOK_SECRET)

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
async def test_webhook_status_change_invalidates_entitlement_cache(
    db_session: AsyncSession,
    webhook_evaluate_client: AsyncClient,
) -> None:
    """payment_failed webhook bumps version after commit; evaluate sees miss + new status."""
    org = await create_organization(
        db_session,
        name="Webhook Invalidate Org",
        external_id=f"ext-wh-inv-{uuid.uuid4().hex[:8]}",
        idempotency_key=f"idem-wh-inv-{uuid.uuid4().hex[:8]}",
    )
    _, org_raw = await create_api_key(
        db_session,
        organization_id=org.id,
        role=ApiKeyRole.PRODUCT_SERVICE.value,
    )

    product = await create_product(
        db_session,
        key=f"wh_inv_prod_{uuid.uuid4().hex[:6]}",
        name="Webhook Invalidate Product",
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
        key=f"wh_inv_plan_{uuid.uuid4().hex[:6]}",
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

    external_sub_id = f"sub_{uuid.uuid4().hex[:12]}"
    now = datetime.now(UTC)
    subscription = Subscription(
        organization_id=org.id,
        plan_id=plan.id,
        status=SubscriptionStatus.active.value,
        current_period_start=now,
        current_period_end=now + timedelta(days=30),
        external_subscription_id=external_sub_id,
    )
    db_session.add(subscription)
    await db_session.commit()

    evaluate_payload = {
        "organization_public_id": str(org.public_id),
        "checks": [{"feature_key": "api_calls", "quantity": 1}],
    }
    headers = {"Authorization": f"Bearer {org_raw}"}

    first = await webhook_evaluate_client.post(
        "/v1/entitlements/evaluate",
        json=evaluate_payload,
        headers=headers,
    )
    assert first.status_code == 200
    first_body = first.json()
    assert first_body["cache_hit"] is False
    assert first_body["subscription_status"] == "active"
    version_before_webhook = first_body["version"]

    second = await webhook_evaluate_client.post(
        "/v1/entitlements/evaluate",
        json=evaluate_payload,
        headers=headers,
    )
    assert second.status_code == 200
    second_body = second.json()
    assert second_body["cache_hit"] is True
    assert second_body["version"] == version_before_webhook

    webhook_payload = {
        "id": f"evt_{uuid.uuid4().hex[:12]}",
        "object": "event",
        "type": "invoice.payment_failed",
        "data": {
            "object": {
                "id": f"in_{uuid.uuid4().hex[:12]}",
                "object": "invoice",
                "subscription": external_sub_id,
                "attempt_count": 1,
            }
        },
    }
    raw = json.dumps(webhook_payload, separators=(",", ":")).encode()
    signature = sign_stripe_payload(raw, WEBHOOK_SECRET)

    webhook_resp = await webhook_evaluate_client.post(
        "/v1/webhooks/mock-stripe",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "Stripe-Signature": signature,
        },
    )
    assert webhook_resp.status_code == 200

    third = await webhook_evaluate_client.post(
        "/v1/entitlements/evaluate",
        json=evaluate_payload,
        headers=headers,
    )
    assert third.status_code == 200
    third_body = third.json()
    assert third_body["cache_hit"] is False
    assert third_body["version"] > version_before_webhook
    assert third_body["subscription_status"] == "past_due"
