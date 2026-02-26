"""Integration: invoice.paid webhook creates ledger entry + outbox event."""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from billing_platform.config import get_settings
from billing_platform.db import get_read_session, get_session
from billing_platform.domain.models.ledger import LedgerEntry, LedgerEntryType
from billing_platform.domain.models.outbox_message import OutboxMessage
from billing_platform.domain.models.subscription import Subscription, SubscriptionStatus
from billing_platform.integrations.mock_stripe.signature import sign_stripe_payload
from billing_platform.main import create_app
from billing_platform.services.catalog import create_plan, create_product, publish_plan
from billing_platform.services.organizations import create_organization

WEBHOOK_SECRET = "whsec_test_ledger"


@pytest_asyncio.fixture
async def webhook_api_client(
    migrated_postgres_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[AsyncClient]:
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
async def test_invoice_paid_creates_ledger_entry(
    db_session: AsyncSession,
    webhook_api_client: AsyncClient,
    migrated_postgres_url: str,
) -> None:
    """POST signed invoice.paid webhook inserts ledger row and ledger.entry_posted outbox."""
    org = await create_organization(
        db_session,
        name="Ledger Activate Org",
        external_id=f"ext-ledger-act-{uuid.uuid4().hex[:8]}",
        idempotency_key=f"idem-org-ledger-act-{uuid.uuid4().hex[:8]}",
    )
    product = await create_product(
        db_session,
        key=f"ledger_prod_{uuid.uuid4().hex[:6]}",
        name="Ledger Product",
    )
    plan = await create_plan(
        db_session,
        product_id=product.id,
        key=f"ledger_plan_{uuid.uuid4().hex[:6]}",
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

    event_id = f"evt_ledger_{uuid.uuid4().hex[:8]}"
    amount_paid = 2500
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
                "amount_paid": amount_paid,
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
        ledger_count = await session.execute(
            select(func.count())
            .select_from(LedgerEntry)
            .where(
                LedgerEntry.organization_id == org.id,
                LedgerEntry.entry_type == LedgerEntryType.invoice_paid.value,
            )
        )
        assert int(ledger_count.scalar_one()) == 1

        entry_result = await session.execute(
            select(LedgerEntry).where(LedgerEntry.organization_id == org.id)
        )
        entry = entry_result.scalar_one()
        assert entry.amount_cents == amount_paid
        assert entry.currency == "USD"
        assert entry.subscription_id is not None

        outbox_count = await session.execute(
            select(func.count())
            .select_from(OutboxMessage)
            .where(OutboxMessage.event_type == "ledger.entry_posted")
        )
        assert int(outbox_count.scalar_one()) == 1

        sub_result = await session.execute(
            select(Subscription).where(Subscription.external_subscription_id == external_sub_id)
        )
        updated = sub_result.scalar_one()
        assert updated.status == SubscriptionStatus.active.value

        outbox_result = await session.execute(
            select(OutboxMessage).where(OutboxMessage.event_type == "ledger.entry_posted")
        )
        outbox_msg = outbox_result.scalar_one()
        ledger_payload = outbox_msg.payload
        assert ledger_payload["entry_public_id"] == str(entry.public_id)
        assert ledger_payload["organization_public_id"] == str(org.public_id)
        assert ledger_payload["subscription_public_id"] == str(updated.public_id)
        assert ledger_payload["amount_cents"] == amount_paid
        assert ledger_payload["metadata"]["invoice_external_id"] == payload["data"]["object"]["id"]
        assert "organization_id" not in ledger_payload
        assert "subscription_id" not in ledger_payload
        assert "invoice_id" not in ledger_payload
        assert "reverses_entry_id" not in ledger_payload
    await engine.dispose()
