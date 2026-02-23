"""Integration: persist-first webhook ingestion (idempotent on provider_event_id)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from billing_platform.config import get_settings
from billing_platform.db import get_read_session, get_session
from billing_platform.domain.models.webhook_event import WebhookEvent
from billing_platform.integrations.mock_stripe.signature import sign_stripe_payload
from billing_platform.main import create_app

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
async def test_webhook_persist_first_and_duplicate_is_idempotent(
    webhook_api_client: AsyncClient,
    migrated_postgres_url: str,
) -> None:
    """Signed webhook is persisted; duplicate provider_event_id returns 200 without second row."""
    payload = {
        "id": "evt_integration_001",
        "object": "event",
        "type": "invoice.paid",
        "data": {"object": {"id": "in_test", "status": "paid"}},
    }
    raw = json.dumps(payload, separators=(",", ":")).encode()
    signature = sign_stripe_payload(raw, WEBHOOK_SECRET)

    first = await webhook_api_client.post(
        "/v1/webhooks/mock-stripe",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "Stripe-Signature": signature,
        },
    )
    assert first.status_code == 200

    engine = create_async_engine(migrated_postgres_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with session_factory() as session:
        count_after_first = await session.scalar(
            select(func.count())
            .select_from(WebhookEvent)
            .where(WebhookEvent.provider_event_id == "evt_integration_001")
        )
        assert count_after_first == 1

        second = await webhook_api_client.post(
            "/v1/webhooks/mock-stripe",
            content=raw,
            headers={
                "Content-Type": "application/json",
                "Stripe-Signature": signature,
            },
        )
        assert second.status_code == 200

        count_after_second = await session.scalar(
            select(func.count())
            .select_from(WebhookEvent)
            .where(WebhookEvent.provider_event_id == "evt_integration_001")
        )
        assert count_after_second == 1
    await engine.dispose()


@pytest.mark.integration
async def test_webhook_invalid_signature_rejected(
    webhook_api_client: AsyncClient,
) -> None:
    """Invalid signature returns 400."""
    raw = b'{"id":"evt_bad","type":"invoice.paid"}'
    response = await webhook_api_client.post(
        "/v1/webhooks/mock-stripe",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "Stripe-Signature": "t=1,v1=deadbeef",
        },
    )
    assert response.status_code == 400
