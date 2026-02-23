"""Unit tests: idempotent webhook processing (no duplicate outbox/ledger)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from billing_platform.domain.models.ledger import LedgerEntry, LedgerEntryType
from billing_platform.domain.models.outbox_message import OutboxMessage
from billing_platform.domain.models.subscription import Subscription, SubscriptionStatus
from billing_platform.domain.models.webhook_event import WebhookEvent, WebhookEventStatus
from billing_platform.services.catalog import create_plan, create_product, publish_plan
from billing_platform.services.organizations import create_organization
from billing_platform.services.webhook_processor import process_webhook
from billing_platform.services.webhooks import persist_webhook


async def count_outbox(session: AsyncSession, *, event_type: str) -> int:
    result = await session.execute(
        select(func.count())
        .select_from(OutboxMessage)
        .where(OutboxMessage.event_type == event_type)
    )
    return int(result.scalar_one())


async def count_ledger(session: AsyncSession, *, entry_type: str) -> int:
    result = await session.execute(
        select(func.count())
        .select_from(LedgerEntry)
        .where(
            LedgerEntry.entry_type == entry_type,
        )
    )
    return int(result.scalar_one())


@pytest.fixture
async def paid_webhook_factory(db_session: AsyncSession):
    """Create subscription + persisted invoice.paid webhook."""

    async def _factory(*, external_sub_id: str | None = None) -> WebhookEvent:
        org = await create_organization(
            db_session,
            name="Webhook Idem Org",
            external_id=f"ext-wh-{uuid.uuid4().hex[:8]}",
            idempotency_key=f"idem-org-wh-{uuid.uuid4().hex[:8]}",
        )
        product = await create_product(
            db_session,
            key=f"wh_prod_{uuid.uuid4().hex[:6]}",
            name="Webhook Product",
        )
        plan = await create_plan(
            db_session,
            product_id=product.id,
            key=f"wh_plan_{uuid.uuid4().hex[:6]}",
            billing_interval="month",
            trial_days=0,
        )
        await publish_plan(db_session, plan.id)

        ext_id = external_sub_id or f"sub_{uuid.uuid4().hex[:12]}"
        now = datetime.now(UTC)
        subscription = Subscription(
            organization_id=org.id,
            plan_id=plan.id,
            status=SubscriptionStatus.incomplete.value,
            current_period_start=now,
            current_period_end=now + timedelta(days=30),
            external_subscription_id=ext_id,
        )
        db_session.add(subscription)
        await db_session.flush()

        invoice_id = f"in_{uuid.uuid4().hex[:12]}"
        payload: dict[str, object] = {
            "id": f"evt_{uuid.uuid4().hex[:12]}",
            "type": "invoice.paid",
            "data": {
                "object": {
                    "id": invoice_id,
                    "object": "invoice",
                    "subscription": ext_id,
                    "status": "paid",
                    "amount_paid": 1000,
                    "currency": "usd",
                }
            },
        }
        webhook = await persist_webhook(
            db_session,
            provider_event_id=str(payload["id"]),
            event_type="invoice.paid",
            payload=payload,
        )
        assert webhook is not None
        return webhook

    return _factory


@pytest.fixture
async def payment_failed_webhook_factory(db_session: AsyncSession):
    """Create active subscription + persisted invoice.payment_failed webhook."""

    async def _factory(*, external_sub_id: str | None = None) -> tuple[WebhookEvent, Subscription]:
        org = await create_organization(
            db_session,
            name="Payment Failed Org",
            external_id=f"ext-pf-{uuid.uuid4().hex[:8]}",
            idempotency_key=f"idem-org-pf-{uuid.uuid4().hex[:8]}",
        )
        product = await create_product(
            db_session,
            key=f"pf_prod_{uuid.uuid4().hex[:6]}",
            name="Payment Failed Product",
        )
        plan = await create_plan(
            db_session,
            product_id=product.id,
            key=f"pf_plan_{uuid.uuid4().hex[:6]}",
            billing_interval="month",
            trial_days=0,
        )
        await publish_plan(db_session, plan.id)

        ext_id = external_sub_id or f"sub_{uuid.uuid4().hex[:12]}"
        now = datetime.now(UTC)
        subscription = Subscription(
            organization_id=org.id,
            plan_id=plan.id,
            status=SubscriptionStatus.active.value,
            current_period_start=now,
            current_period_end=now + timedelta(days=30),
            external_subscription_id=ext_id,
        )
        db_session.add(subscription)
        await db_session.flush()

        invoice_id = f"in_{uuid.uuid4().hex[:12]}"
        payload: dict[str, object] = {
            "id": f"evt_{uuid.uuid4().hex[:12]}",
            "type": "invoice.payment_failed",
            "data": {
                "object": {
                    "id": invoice_id,
                    "object": "invoice",
                    "subscription": ext_id,
                    "status": "open",
                    "attempt_count": 1,
                    "next_payment_attempt": "2026-02-24T00:00:00Z",
                }
            },
        }
        webhook = await persist_webhook(
            db_session,
            provider_event_id=str(payload["id"]),
            event_type="invoice.payment_failed",
            payload=payload,
        )
        assert webhook is not None
        return webhook, subscription

    return _factory


@pytest.mark.asyncio
async def test_payment_failed_transitions_past_due_and_emits_event(
    db_session: AsyncSession,
    payment_failed_webhook_factory,
) -> None:
    webhook, subscription = await payment_failed_webhook_factory()
    await process_webhook(db_session, webhook.id)
    await db_session.refresh(subscription)
    assert subscription.status == SubscriptionStatus.past_due.value
    assert await count_outbox(db_session, event_type="subscription.payment_failed") == 1
    assert await count_outbox(db_session, event_type="subscription.past_due") == 1

    for event_type in ("subscription.payment_failed", "subscription.past_due"):
        result = await db_session.execute(
            select(OutboxMessage).where(OutboxMessage.event_type == event_type)
        )
        row = result.scalar_one()
        assert "organization_public_id" in row.payload
        assert "subscription_public_id" in row.payload
        assert "organization_id" not in row.payload
        assert "subscription_id" not in row.payload


@pytest.mark.asyncio
async def test_duplicate_payment_failed_does_not_double_outbox(
    db_session: AsyncSession,
    payment_failed_webhook_factory,
) -> None:
    webhook, subscription = await payment_failed_webhook_factory()
    await process_webhook(db_session, webhook.id)
    await process_webhook(db_session, webhook.id)
    await db_session.refresh(subscription)
    assert subscription.status == SubscriptionStatus.past_due.value
    assert await count_outbox(db_session, event_type="subscription.payment_failed") == 1
    assert await count_outbox(db_session, event_type="subscription.past_due") == 1


@pytest.mark.asyncio
async def test_duplicate_webhook_does_not_double_outbox(
    db_session: AsyncSession,
    paid_webhook_factory,
) -> None:
    wh = await paid_webhook_factory()
    await process_webhook(db_session, wh.id)
    await process_webhook(db_session, wh.id)
    assert await count_outbox(db_session, event_type="subscription.activated") == 1
    assert await count_ledger(db_session, entry_type=LedgerEntryType.invoice_paid.value) == 1


@pytest.mark.asyncio
async def test_poison_webhook_marked_failed_without_crash(
    db_session: AsyncSession,
) -> None:
    """Malformed payload → failed status + last_error; second call is a no-op."""
    webhook = await persist_webhook(
        db_session,
        provider_event_id=f"evt_poison_{uuid.uuid4().hex[:8]}",
        event_type="invoice.paid",
        payload={"id": "evt_poison", "type": "invoice.paid", "data": {"object": {}}},
    )
    assert webhook is not None

    await process_webhook(db_session, webhook.id)
    await db_session.refresh(webhook)
    assert webhook.status == WebhookEventStatus.FAILED
    assert webhook.last_error is not None

    await process_webhook(db_session, webhook.id)
    await db_session.refresh(webhook)
    assert webhook.processing_attempts == 1
    assert await count_outbox(db_session, event_type="subscription.activated") == 0
