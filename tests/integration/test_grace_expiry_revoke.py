"""Integration: grace expiry revokes access, bumps cache, emits outbox."""

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
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.community.redis import RedisContainer

from billing_platform.config import get_settings
from billing_platform.db import get_read_session, get_session
from billing_platform.db.session import close_db_engine, reset_db_singletons
from billing_platform.domain.models.api_key import ApiKeyRole
from billing_platform.domain.models.ledger import LedgerEntry, LedgerEntryType
from billing_platform.domain.models.outbox_message import OutboxMessage
from billing_platform.domain.models.subscription import Subscription, SubscriptionStatus
from billing_platform.integrations.redis_cache import (
    close_redis_client,
    get_redis_client,
    increment_entitlement_version,
)
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
from billing_platform.services.grace import enforce_grace_expiry, is_grace_active
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
async def grace_evaluate_client(
    migrated_postgres_url: str,
    redis_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[AsyncClient]:
    """API client with Postgres + Redis for grace integration tests."""
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


async def _seed_past_due_subscription(
    db_session: AsyncSession,
    *,
    grace_period_days: int,
    entered_at: datetime,
    enforcement_mode: str = "degraded",
) -> tuple[object, Subscription, object]:
    org = await create_organization(
        db_session,
        name="Grace Expiry Org",
        external_id=f"ext-grace-{uuid.uuid4().hex[:8]}",
        idempotency_key=f"idem-grace-{uuid.uuid4().hex[:8]}",
    )
    _, org_raw = await create_api_key(
        db_session,
        organization_id=org.id,
        role=ApiKeyRole.PRODUCT_SERVICE.value,
    )

    product = await create_product(
        db_session,
        key=f"grace_prod_{uuid.uuid4().hex[:6]}",
        name="Grace Product",
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
        key=f"grace_plan_{uuid.uuid4().hex[:6]}",
        billing_interval="month",
        trial_days=0,
        grace_period_days=grace_period_days,
    )
    await set_plan_features(
        db_session,
        plan.id,
        [
            PlanFeatureInput(
                feature_id=feature.id,
                limit_value=100,
                is_enabled=True,
                enforcement_mode=enforcement_mode,
            )
        ],
    )
    await publish_plan(db_session, plan.id)

    now = datetime.now(UTC)
    subscription = Subscription(
        organization_id=org.id,
        plan_id=plan.id,
        status=SubscriptionStatus.past_due.value,
        current_period_start=now,
        current_period_end=now + timedelta(days=30),
        past_due_entered_at=entered_at,
    )
    db_session.add(subscription)
    await db_session.commit()

    return org, subscription, org_raw


@pytest.mark.integration
async def test_within_grace_not_revoked(
    db_session: AsyncSession,
    grace_evaluate_client: AsyncClient,
) -> None:
    """past_due within grace window keeps degraded access."""
    entered = datetime.now(UTC) - timedelta(days=2)
    org, subscription, org_raw = await _seed_past_due_subscription(
        db_session,
        grace_period_days=7,
        entered_at=entered,
    )
    await db_session.refresh(subscription)

    assert is_grace_active(
        status=subscription.status,
        grace_period_days=7,
        past_due_entered_at=subscription.past_due_entered_at,
        now=datetime.now(UTC),
    )

    payload = {
        "organization_public_id": str(org.public_id),
        "checks": [{"feature_key": "api_calls", "quantity": 1}],
    }
    response = await grace_evaluate_client.post(
        "/v1/entitlements/evaluate",
        json=payload,
        headers={"Authorization": f"Bearer {org_raw}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["subscription_status"] == "past_due"
    assert body["results"][0]["allowed"] is True


@pytest.mark.integration
async def test_grace_expiry_revokes_access_and_bumps_cache(
    db_session: AsyncSession,
    grace_evaluate_client: AsyncClient,
) -> None:
    """After grace expiry, enforce transitions to unpaid and denies evaluate with cache bump."""
    entered = datetime.now(UTC) - timedelta(days=8)
    org, subscription, org_raw = await _seed_past_due_subscription(
        db_session,
        grace_period_days=7,
        entered_at=entered,
    )
    await db_session.refresh(subscription)

    evaluate_payload = {
        "organization_public_id": str(org.public_id),
        "checks": [{"feature_key": "api_calls", "quantity": 1}],
    }
    headers = {"Authorization": f"Bearer {org_raw}"}

    before = await grace_evaluate_client.post(
        "/v1/entitlements/evaluate",
        json=evaluate_payload,
        headers=headers,
    )
    assert before.status_code == 200
    before_body = before.json()
    assert before_body["subscription_status"] == "past_due"
    assert before_body["results"][0]["allowed"] is False
    assert before_body["results"][0]["reason"] == "subscription_access_revoked"
    version_before_enforce = before_body["version"]

    now = datetime.now(UTC)
    processed, org_ids = await enforce_grace_expiry(db_session, now=now)
    await db_session.commit()
    assert processed == 1
    if org_ids:
        redis = await get_redis_client()
        for organization_id in org_ids:
            await increment_entitlement_version(redis, organization_id=organization_id)

    outbox_row = await db_session.scalar(
        select(OutboxMessage).where(OutboxMessage.event_type == "subscription.access_revoked")
    )
    assert outbox_row is not None
    assert "organization_public_id" in outbox_row.payload
    assert "organization_id" not in outbox_row.payload
    assert "subscription_public_id" in outbox_row.payload
    assert "subscription_id" not in outbox_row.payload

    await db_session.refresh(subscription)
    assert subscription.status == SubscriptionStatus.unpaid.value
    assert subscription.past_due_entered_at is None

    outbox_count = await db_session.scalar(
        select(func.count())
        .select_from(OutboxMessage)
        .where(OutboxMessage.event_type == "subscription.access_revoked")
    )
    assert outbox_count == 1

    ledger_count = await db_session.scalar(
        select(func.count())
        .select_from(LedgerEntry)
        .where(LedgerEntry.entry_type == LedgerEntryType.access_revoked_marker.value)
    )
    assert ledger_count == 1

    after = await grace_evaluate_client.post(
        "/v1/entitlements/evaluate",
        json=evaluate_payload,
        headers=headers,
    )
    assert after.status_code == 200
    after_body = after.json()
    assert after_body["subscription_status"] == "unpaid"
    assert after_body["results"][0]["allowed"] is False
    assert after_body["cache_hit"] is False
    assert after_body["version"] > version_before_enforce
