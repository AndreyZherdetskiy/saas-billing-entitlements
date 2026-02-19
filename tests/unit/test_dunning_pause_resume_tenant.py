"""Unit tests: pause/resume require organization_id filter (defense-in-depth)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from billing_platform.domain.models.subscription import Subscription, SubscriptionStatus
from billing_platform.services.catalog import create_plan, create_product, publish_plan
from billing_platform.services.dunning import pause_campaign, resume_campaign, start_campaign
from billing_platform.services.organizations import create_organization


@pytest.mark.asyncio
async def test_pause_campaign_wrong_organization_returns_not_found(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DUNNING_ENABLED", "true")
    from billing_platform.config import get_settings

    get_settings.cache_clear()

    org_a = await create_organization(
        db_session,
        name="Org A",
        external_id=f"ext-a-{uuid.uuid4().hex[:8]}",
        idempotency_key=f"idem-a-{uuid.uuid4().hex[:8]}",
    )
    org_b = await create_organization(
        db_session,
        name="Org B",
        external_id=f"ext-b-{uuid.uuid4().hex[:8]}",
        idempotency_key=f"idem-b-{uuid.uuid4().hex[:8]}",
    )
    product = await create_product(
        db_session,
        key=f"tenant_prod_{uuid.uuid4().hex[:6]}",
        name="Tenant Product",
    )
    plan = await create_plan(
        db_session,
        product_id=product.id,
        key=f"tenant_plan_{uuid.uuid4().hex[:6]}",
        billing_interval="month",
        trial_days=0,
    )
    await publish_plan(db_session, plan.id)

    started_at = datetime(2026, 2, 16, 12, 0, tzinfo=UTC)
    subscription = Subscription(
        organization_id=org_a.id,
        plan_id=plan.id,
        status=SubscriptionStatus.past_due.value,
        current_period_start=started_at,
        current_period_end=started_at + timedelta(days=30),
        past_due_entered_at=started_at,
    )
    db_session.add(subscription)
    await db_session.flush()

    campaign = await start_campaign(
        db_session,
        subscription_id=subscription.id,
        organization_id=org_a.id,
        idempotency_key=f"dunning:tenant:{uuid.uuid4().hex[:8]}",
        started_at=started_at,
    )
    assert campaign is not None

    with pytest.raises(ValueError, match="not found"):
        await pause_campaign(
            db_session,
            campaign_public_id=campaign.id,
            organization_id=org_b.id,
            actor_key_id=uuid.uuid4(),
        )


@pytest.mark.asyncio
async def test_resume_campaign_wrong_organization_returns_not_found(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DUNNING_ENABLED", "true")
    from billing_platform.config import get_settings

    get_settings.cache_clear()

    org_a = await create_organization(
        db_session,
        name="Org A Resume",
        external_id=f"ext-ar-{uuid.uuid4().hex[:8]}",
        idempotency_key=f"idem-ar-{uuid.uuid4().hex[:8]}",
    )
    org_b = await create_organization(
        db_session,
        name="Org B Resume",
        external_id=f"ext-br-{uuid.uuid4().hex[:8]}",
        idempotency_key=f"idem-br-{uuid.uuid4().hex[:8]}",
    )
    product = await create_product(
        db_session,
        key=f"resume_prod_{uuid.uuid4().hex[:6]}",
        name="Resume Product",
    )
    plan = await create_plan(
        db_session,
        product_id=product.id,
        key=f"resume_plan_{uuid.uuid4().hex[:6]}",
        billing_interval="month",
        trial_days=0,
    )
    await publish_plan(db_session, plan.id)

    started_at = datetime(2026, 2, 16, 12, 0, tzinfo=UTC)
    subscription = Subscription(
        organization_id=org_a.id,
        plan_id=plan.id,
        status=SubscriptionStatus.past_due.value,
        current_period_start=started_at,
        current_period_end=started_at + timedelta(days=30),
        past_due_entered_at=started_at,
    )
    db_session.add(subscription)
    await db_session.flush()

    campaign = await start_campaign(
        db_session,
        subscription_id=subscription.id,
        organization_id=org_a.id,
        idempotency_key=f"dunning:resume:{uuid.uuid4().hex[:8]}",
        started_at=started_at,
    )
    assert campaign is not None

    await pause_campaign(
        db_session,
        campaign_public_id=campaign.id,
        organization_id=org_a.id,
        actor_key_id=uuid.uuid4(),
    )

    with pytest.raises(ValueError, match="not found"):
        await resume_campaign(
            db_session,
            campaign_public_id=campaign.id,
            organization_id=org_b.id,
            actor_key_id=uuid.uuid4(),
        )
