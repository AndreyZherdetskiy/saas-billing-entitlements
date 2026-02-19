"""Unit tests: dunning campaign creation and idempotency."""

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
from billing_platform.services.dunning import start_campaign
from billing_platform.services.organizations import create_organization


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


@pytest.fixture
async def past_due_subscription(db_session: AsyncSession) -> Subscription:
    org = await create_organization(
        db_session,
        name="Dunning Org",
        external_id=f"ext-dun-{uuid.uuid4().hex[:8]}",
        idempotency_key=f"idem-org-dun-{uuid.uuid4().hex[:8]}",
    )
    product = await create_product(
        db_session,
        key=f"dun_prod_{uuid.uuid4().hex[:6]}",
        name="Dunning Product",
    )
    plan = await create_plan(
        db_session,
        product_id=product.id,
        key=f"dun_plan_{uuid.uuid4().hex[:6]}",
        billing_interval="month",
        trial_days=0,
        grace_period_days=7,
    )
    await publish_plan(db_session, plan.id)

    now = datetime.now(UTC)
    subscription = Subscription(
        organization_id=org.id,
        plan_id=plan.id,
        status=SubscriptionStatus.past_due.value,
        current_period_start=now,
        current_period_end=now + timedelta(days=30),
        external_subscription_id=f"sub_{uuid.uuid4().hex[:12]}",
        past_due_entered_at=now,
    )
    db_session.add(subscription)
    await db_session.flush()
    return subscription


@pytest.mark.asyncio
async def test_start_campaign_when_enabled_creates_one_row(
    db_session: AsyncSession,
    past_due_subscription: Subscription,
    dunning_enabled: None,
) -> None:
    idem_key = f"dunning:test:{uuid.uuid4().hex[:8]}"
    campaign = await start_campaign(
        db_session,
        subscription_id=past_due_subscription.id,
        organization_id=past_due_subscription.organization_id,
        idempotency_key=idem_key,
    )

    assert campaign is not None
    assert campaign.status == DunningCampaignStatus.ACTIVE.value
    assert campaign.subscription_id == past_due_subscription.id
    assert campaign.organization_id == past_due_subscription.organization_id
    assert campaign.grace_until is not None
    assert await count_campaigns(db_session) == 1


@pytest.mark.asyncio
async def test_start_campaign_when_disabled_returns_none(
    db_session: AsyncSession,
    past_due_subscription: Subscription,
    dunning_disabled: None,
) -> None:
    campaign = await start_campaign(
        db_session,
        subscription_id=past_due_subscription.id,
        organization_id=past_due_subscription.organization_id,
        idempotency_key=f"dunning:disabled:{uuid.uuid4().hex[:8]}",
    )

    assert campaign is None
    assert await count_campaigns(db_session) == 0


@pytest.mark.asyncio
async def test_duplicate_idempotency_key_creates_one_row(
    db_session: AsyncSession,
    past_due_subscription: Subscription,
    dunning_enabled: None,
) -> None:
    idem_key = f"dunning:dup:{uuid.uuid4().hex[:8]}"

    first = await start_campaign(
        db_session,
        subscription_id=past_due_subscription.id,
        organization_id=past_due_subscription.organization_id,
        idempotency_key=idem_key,
    )
    second = await start_campaign(
        db_session,
        subscription_id=past_due_subscription.id,
        organization_id=past_due_subscription.organization_id,
        idempotency_key=idem_key,
    )

    assert first is not None
    assert second is not None
    assert first.id == second.id
    assert await count_campaigns(db_session) == 1
