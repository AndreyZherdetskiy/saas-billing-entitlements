"""Integration: payment_failed webhook starts dunning when enabled."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from billing_platform.config import get_settings
from billing_platform.domain.models.dunning import DunningCampaign, DunningCampaignStatus
from billing_platform.domain.models.subscription import Subscription, SubscriptionStatus
from billing_platform.services.catalog import create_plan, create_product, publish_plan
from billing_platform.services.organizations import create_organization
from billing_platform.services.webhook_processor import process_webhook
from billing_platform.services.webhooks import persist_webhook

pytestmark = pytest.mark.integration


async def count_campaigns(session: AsyncSession) -> int:
    result = await session.execute(select(func.count()).select_from(DunningCampaign))
    return int(result.scalar_one())


@pytest.fixture
def dunning_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DUNNING_ENABLED", "true")
    get_settings.cache_clear()


@pytest.fixture
def dunning_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DUNNING_ENABLED", "false")
    get_settings.cache_clear()


async def _create_payment_failed_webhook(
    db_session: AsyncSession,
) -> tuple[object, Subscription]:
    org = await create_organization(
        db_session,
        name="Dunning Webhook Org",
        external_id=f"ext-dwh-{uuid.uuid4().hex[:8]}",
        idempotency_key=f"idem-org-dwh-{uuid.uuid4().hex[:8]}",
    )
    product = await create_product(
        db_session,
        key=f"dwh_prod_{uuid.uuid4().hex[:6]}",
        name="Dunning Webhook Product",
    )
    plan = await create_plan(
        db_session,
        product_id=product.id,
        key=f"dwh_plan_{uuid.uuid4().hex[:6]}",
        billing_interval="month",
        trial_days=0,
        grace_period_days=7,
    )
    await publish_plan(db_session, plan.id)

    ext_id = f"sub_{uuid.uuid4().hex[:12]}"
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

    payload: dict[str, object] = {
        "id": f"evt_{uuid.uuid4().hex[:12]}",
        "type": "invoice.payment_failed",
        "data": {
            "object": {
                "id": f"in_{uuid.uuid4().hex[:12]}",
                "object": "invoice",
                "subscription": ext_id,
                "status": "open",
                "attempt_count": 1,
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


@pytest.mark.asyncio
async def test_payment_failed_starts_dunning_when_enabled(
    db_session: AsyncSession,
    dunning_enabled: None,
) -> None:
    webhook, subscription = await _create_payment_failed_webhook(db_session)

    await process_webhook(db_session, webhook.id)
    await db_session.refresh(subscription)

    assert subscription.status == SubscriptionStatus.past_due.value
    assert await count_campaigns(db_session) == 1

    result = await db_session.execute(
        select(DunningCampaign).where(DunningCampaign.subscription_id == subscription.id)
    )
    campaign = result.scalar_one()
    assert campaign.status == DunningCampaignStatus.ACTIVE.value
    assert campaign.organization_id == subscription.organization_id
    assert campaign.idempotency_key == f"webhook:{webhook.id}:dunning_campaign"


@pytest.mark.asyncio
async def test_payment_failed_skips_dunning_when_disabled(
    db_session: AsyncSession,
    dunning_disabled: None,
) -> None:
    webhook, subscription = await _create_payment_failed_webhook(db_session)

    await process_webhook(db_session, webhook.id)
    await db_session.refresh(subscription)

    assert subscription.status == SubscriptionStatus.past_due.value
    assert await count_campaigns(db_session) == 0
