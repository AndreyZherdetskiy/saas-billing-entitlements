"""Integration: admin webhook replay API (E4-01)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from billing_platform.domain.models.api_key import ApiKeyRole
from billing_platform.domain.models.ledger import LedgerEntry, LedgerEntryType
from billing_platform.domain.models.outbox_message import OutboxMessage
from billing_platform.domain.models.subscription import Subscription, SubscriptionStatus
from billing_platform.domain.models.webhook_event import WebhookEvent, WebhookEventStatus
from billing_platform.services.api_keys import create_api_key
from billing_platform.services.catalog import create_plan, create_product, publish_plan
from billing_platform.services.organizations import create_organization
from billing_platform.services.webhook_processor import process_webhook
from billing_platform.services.webhooks import persist_webhook


async def _create_failed_paid_webhook(db_session: AsyncSession) -> tuple[WebhookEvent, str]:
    """Persist invoice.paid webhook, fail processing, then fix payload for replay."""
    org = await create_organization(
        db_session,
        name="Replay Org",
        external_id=f"ext-replay-{uuid.uuid4().hex[:8]}",
        idempotency_key=f"idem-replay-org-{uuid.uuid4().hex[:8]}",
    )
    product = await create_product(
        db_session,
        key=f"replay_prod_{uuid.uuid4().hex[:6]}",
        name="Replay Product",
    )
    plan = await create_plan(
        db_session,
        product_id=product.id,
        key=f"replay_plan_{uuid.uuid4().hex[:6]}",
        billing_interval="month",
        trial_days=0,
    )
    await publish_plan(db_session, plan.id)

    ext_id = f"sub_{uuid.uuid4().hex[:12]}"
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

    payload: dict[str, object] = {
        "id": f"evt_{uuid.uuid4().hex[:12]}",
        "type": "invoice.paid",
        "data": {
            "object": {
                "id": f"in_{uuid.uuid4().hex[:12]}",
                "object": "invoice",
                "subscription": "sub_missing_before_fix",
                "status": "paid",
                "amount_paid": 1500,
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

    await process_webhook(db_session, webhook.id)
    await db_session.refresh(webhook)
    assert webhook.status == WebhookEventStatus.FAILED

    payload["data"] = {
        "object": {
            "id": f"in_{uuid.uuid4().hex[:12]}",
            "object": "invoice",
            "subscription": ext_id,
            "status": "paid",
            "amount_paid": 1500,
            "currency": "usd",
        }
    }
    webhook.payload = payload
    await db_session.flush()
    await db_session.commit()

    _, admin_raw = await create_api_key(
        db_session,
        organization_id=None,
        role=ApiKeyRole.PLATFORM_ADMIN.value,
    )
    await db_session.commit()
    return webhook, admin_raw


@pytest.mark.integration
async def test_webhook_replay_requires_platform_admin(
    api_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Tenant API keys cannot replay webhooks."""
    org = await create_organization(
        db_session,
        name="Tenant Replay Org",
        external_id=f"ext-tenant-replay-{uuid.uuid4().hex[:8]}",
        idempotency_key=f"idem-tenant-replay-{uuid.uuid4().hex[:8]}",
    )
    _, tenant_raw = await create_api_key(
        db_session,
        organization_id=org.id,
        role=ApiKeyRole.PRODUCT_SERVICE.value,
    )
    await db_session.commit()

    response = await api_client.post(
        f"/v1/admin/webhooks/{uuid.uuid4()}/replay",
        headers={"Authorization": f"Bearer {tenant_raw}"},
    )
    assert response.status_code == 403


@pytest.mark.integration
async def test_webhook_replay_reprocesses_failed_event(
    api_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Failed webhook is replayed idempotently via admin route."""
    webhook, admin_raw = await _create_failed_paid_webhook(db_session)
    headers = {"Authorization": f"Bearer {admin_raw}"}

    ledger_before = await db_session.scalar(select(func.count()).select_from(LedgerEntry))
    outbox_before = await db_session.scalar(
        select(func.count())
        .select_from(OutboxMessage)
        .where(OutboxMessage.event_type == "subscription.activated")
    )

    response = await api_client.post(
        f"/v1/admin/webhooks/{webhook.id}/replay",
        headers=headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(webhook.id)
    assert body["replay_result"] == "replayed"
    assert body["status"] == WebhookEventStatus.PROCESSED.value

    await db_session.refresh(webhook)
    assert webhook.status == WebhookEventStatus.PROCESSED
    assert webhook.processed_at is not None

    ledger_after = await db_session.scalar(select(func.count()).select_from(LedgerEntry))
    outbox_after = await db_session.scalar(
        select(func.count())
        .select_from(OutboxMessage)
        .where(OutboxMessage.event_type == "subscription.activated")
    )
    assert ledger_after == ledger_before + 1
    assert outbox_after == outbox_before + 1

    ledger_type_count = await db_session.scalar(
        select(func.count())
        .select_from(LedgerEntry)
        .where(LedgerEntry.entry_type == LedgerEntryType.invoice_paid.value)
    )
    assert ledger_type_count == 1

    second = await api_client.post(
        f"/v1/admin/webhooks/{webhook.id}/replay",
        headers=headers,
    )
    assert second.status_code == 200
    assert second.json()["replay_result"] == "already_processed"

    ledger_final = await db_session.scalar(select(func.count()).select_from(LedgerEntry))
    outbox_final = await db_session.scalar(
        select(func.count())
        .select_from(OutboxMessage)
        .where(OutboxMessage.event_type == "subscription.activated")
    )
    assert ledger_final == ledger_after
    assert outbox_final == outbox_after
