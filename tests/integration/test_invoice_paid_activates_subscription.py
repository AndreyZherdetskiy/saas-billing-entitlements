"""Integration: invoice.paid webhook activates subscription."""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from billing_platform.config import get_settings
from billing_platform.db import get_read_session, get_session
from billing_platform.domain.models.subscription import Subscription, SubscriptionStatus
from billing_platform.integrations.mock_stripe.signature import sign_stripe_payload
from billing_platform.main import create_app
from billing_platform.services.catalog import create_plan, create_product, publish_plan
from billing_platform.services.organizations import create_organization

WEBHOOK_SECRET = "whsec_test_integration"


@pytest_asyncio.fixture
async def webhook_api_client(
    migrated_postgres_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[AsyncClient]:
    """API client with webhook secret configured."""
    get_settings.cache_clear()
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
    await engine.dispose()


@pytest.mark.integration
async def test_invoice_paid_webhook_activates_subscription(
    db_session: AsyncSession,
    webhook_api_client: AsyncClient,
    migrated_postgres_url: str,
) -> None:
    """POST signed invoice.paid webhook transitions incomplete subscription to active."""
    org = await create_organization(
        db_session,
        name="Paid Activate Org",
        external_id=f"ext-paid-{uuid.uuid4().hex[:8]}",
        idempotency_key=f"idem-org-paid-{uuid.uuid4().hex[:8]}",
    )
    product = await create_product(
        db_session,
        key=f"paid_prod_{uuid.uuid4().hex[:6]}",
        name="Paid Product",
    )
    plan = await create_plan(
        db_session,
        product_id=product.id,
        key=f"paid_plan_{uuid.uuid4().hex[:6]}",
        billing_interval="month",
        trial_days=0,
    )
    await publish_plan(db_session, plan.id)

    external_sub_id = f"sub_{uuid.uuid4().hex[:12]}"
    now = datetime.now(UTC)
    subscription = Subscription(
        organization_id=org.id,
        plan_id=plan.id,
        status=SubscriptionStatus.incomplete.value,
        current_period_start=now,
        current_period_end=now + timedelta(days=30),
        external_subscription_id=external_sub_id,
    )
    db_session.add(subscription)
    await db_session.commit()

    event_id = f"evt_paid_{uuid.uuid4().hex[:8]}"
    payload = {
        "id": event_id,
        "object": "event",
        "type": "invoice.paid",
        "data": {
            "object": {
                "id": f"in_{uuid.uuid4().hex[:8]}",
                "object": "invoice",
                "subscription": external_sub_id,
                "status": "paid",
                "amount_paid": 1000,
                "currency": "usd",
            }
        },
    }
    raw_body = json.dumps(payload, separators=(",", ":")).encode()
    signature = sign_stripe_payload(raw_body, WEBHOOK_SECRET)

    response = await webhook_api_client.post(
        "/v1/webhooks/mock-stripe",
        content=raw_body,
        headers={
            "Content-Type": "application/json",
            "Stripe-Signature": signature,
        },
    )
    assert response.status_code == 200

    engine = create_async_engine(migrated_postgres_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        result = await session.execute(
            select(Subscription).where(Subscription.external_subscription_id == external_sub_id)
        )
        updated = result.scalar_one()
        assert updated.status == SubscriptionStatus.active.value
    await engine.dispose()
