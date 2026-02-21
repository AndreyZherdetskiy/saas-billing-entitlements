"""Unit tests for subscription service helpers."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from billing_platform.domain.models.subscription import SubscriptionStatus
from billing_platform.services.catalog import create_plan, create_product, publish_plan
from billing_platform.services.organizations import create_organization
from billing_platform.services.subscriptions import (
    PlanNotPublishedError,
    cancel_subscription,
    create_subscription,
    get_subscription_by_public_id,
    list_subscriptions_for_organization,
)


@pytest.mark.asyncio
async def test_create_subscription_rejects_unpublished_plan(db_session: AsyncSession) -> None:
    org = await create_organization(
        db_session,
        name="Unpub Org",
        external_id=f"ext-unpub-{uuid.uuid4().hex[:8]}",
        idempotency_key=f"idem-unpub-{uuid.uuid4().hex[:8]}",
    )
    product = await create_product(
        db_session,
        key=f"unpub_prod_{uuid.uuid4().hex[:6]}",
        name="Unpublished",
    )
    plan = await create_plan(
        db_session,
        product_id=product.id,
        key=f"unpub_plan_{uuid.uuid4().hex[:6]}",
        billing_interval="month",
        trial_days=0,
    )

    with pytest.raises(PlanNotPublishedError):
        await create_subscription(
            db_session,
            organization_id=org.id,
            plan_id=plan.id,
            idempotency_key=f"idem-sub-unpub-{uuid.uuid4().hex[:8]}",
        )


@pytest.mark.asyncio
async def test_create_subscription_yearly_period_end(db_session: AsyncSession) -> None:
    org = await create_organization(
        db_session,
        name="Yearly Org",
        external_id=f"ext-year-{uuid.uuid4().hex[:8]}",
        idempotency_key=f"idem-year-{uuid.uuid4().hex[:8]}",
    )
    product = await create_product(
        db_session,
        key=f"year_prod_{uuid.uuid4().hex[:6]}",
        name="Yearly",
    )

    plan = await create_plan(
        db_session,
        product_id=product.id,
        key=f"year_plan_{uuid.uuid4().hex[:6]}",
        billing_interval="year",
        trial_days=0,
    )
    await publish_plan(db_session, plan.id)

    before = datetime.now(UTC)
    subscription = await create_subscription(
        db_session,
        organization_id=org.id,
        plan_id=plan.id,
        idempotency_key=f"idem-sub-year-{uuid.uuid4().hex[:8]}",
    )
    period_end = subscription.current_period_end
    assert period_end >= before + timedelta(days=364)


@pytest.mark.asyncio
async def test_cancel_subscription_at_period_end(db_session: AsyncSession) -> None:
    org = await create_organization(
        db_session,
        name="Cancel End Org",
        external_id=f"ext-cancel-end-{uuid.uuid4().hex[:8]}",
        idempotency_key=f"idem-cancel-end-{uuid.uuid4().hex[:8]}",
    )
    product = await create_product(
        db_session,
        key=f"cancel_end_prod_{uuid.uuid4().hex[:6]}",
        name="Cancel End",
    )

    plan = await create_plan(
        db_session,
        product_id=product.id,
        key=f"cancel_end_plan_{uuid.uuid4().hex[:6]}",
        billing_interval="month",
        trial_days=14,
    )
    await publish_plan(db_session, plan.id)

    subscription = await create_subscription(
        db_session,
        organization_id=org.id,
        plan_id=plan.id,
        idempotency_key=f"idem-sub-cancel-end-{uuid.uuid4().hex[:8]}",
    )
    canceled = await cancel_subscription(db_session, subscription, at_period_end=True)
    assert canceled.cancel_at_period_end is True
    assert canceled.status == SubscriptionStatus.trialing.value


@pytest.mark.asyncio
async def test_get_and_list_subscriptions(db_session: AsyncSession) -> None:
    org = await create_organization(
        db_session,
        name="List Org",
        external_id=f"ext-list-{uuid.uuid4().hex[:8]}",
        idempotency_key=f"idem-list-{uuid.uuid4().hex[:8]}",
    )
    product = await create_product(
        db_session,
        key=f"list_prod_{uuid.uuid4().hex[:6]}",
        name="List",
    )

    plan = await create_plan(
        db_session,
        product_id=product.id,
        key=f"list_plan_{uuid.uuid4().hex[:6]}",
        billing_interval="month",
        trial_days=0,
    )
    await publish_plan(db_session, plan.id)

    subscription = await create_subscription(
        db_session,
        organization_id=org.id,
        plan_id=plan.id,
        idempotency_key=f"idem-sub-list-{uuid.uuid4().hex[:8]}",
    )
    await db_session.commit()

    loaded = await get_subscription_by_public_id(db_session, str(subscription.public_id))
    listed = await list_subscriptions_for_organization(db_session, organization_id=org.id)
    assert loaded is not None
    assert loaded.id == subscription.id
    assert len(listed) == 1
    assert listed[0].id == subscription.id
