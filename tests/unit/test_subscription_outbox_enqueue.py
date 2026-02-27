"""Unit tests: subscription domain paths enqueue outbox events."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from billing_platform.domain.models.outbox_message import OutboxMessage
from billing_platform.domain.models.subscription import Subscription, SubscriptionStatus
from billing_platform.services.catalog import create_plan, create_product, publish_plan
from billing_platform.services.organizations import create_organization
from billing_platform.services.subscriptions import cancel_subscription, create_subscription


async def _count_outbox(session: AsyncSession, *, event_type: str) -> int:
    result = await session.execute(
        select(func.count())
        .select_from(OutboxMessage)
        .where(OutboxMessage.event_type == event_type)
    )
    return int(result.scalar_one())


@pytest.mark.asyncio
async def test_create_subscription_trialing_enqueues_trial_started(
    db_session: AsyncSession,
) -> None:
    org = await create_organization(
        db_session,
        name="Trial Org",
        external_id=f"ext-trial-{uuid.uuid4().hex[:8]}",
        idempotency_key=f"idem-org-trial-{uuid.uuid4().hex[:8]}",
    )
    product = await create_product(
        db_session,
        key=f"trial_prod_{uuid.uuid4().hex[:6]}",
        name="Trial",
    )
    plan = await create_plan(
        db_session,
        product_id=product.id,
        key=f"trial_plan_{uuid.uuid4().hex[:6]}",
        billing_interval="month",
        trial_days=14,
    )
    await publish_plan(db_session, plan.id)

    subscription = await create_subscription(
        db_session,
        organization_id=org.id,
        plan_id=plan.id,
        idempotency_key=f"idem-sub-trial-{uuid.uuid4().hex[:8]}",
    )
    await db_session.commit()

    assert subscription.status == SubscriptionStatus.trialing.value
    assert await _count_outbox(db_session, event_type="subscription.trial_started") == 1


@pytest.mark.asyncio
async def test_create_subscription_without_trial_skips_trial_started(
    db_session: AsyncSession,
) -> None:
    org = await create_organization(
        db_session,
        name="No Trial Org",
        external_id=f"ext-nt-{uuid.uuid4().hex[:8]}",
        idempotency_key=f"idem-org-nt-{uuid.uuid4().hex[:8]}",
    )
    product = await create_product(
        db_session,
        key=f"nt_prod_{uuid.uuid4().hex[:6]}",
        name="No Trial",
    )
    plan = await create_plan(
        db_session,
        product_id=product.id,
        key=f"nt_plan_{uuid.uuid4().hex[:6]}",
        billing_interval="month",
        trial_days=0,
    )
    await publish_plan(db_session, plan.id)

    subscription = await create_subscription(
        db_session,
        organization_id=org.id,
        plan_id=plan.id,
        idempotency_key=f"idem-sub-nt-{uuid.uuid4().hex[:8]}",
    )
    await db_session.commit()

    assert subscription.status == SubscriptionStatus.incomplete.value
    assert await _count_outbox(db_session, event_type="subscription.trial_started") == 0


@pytest.mark.asyncio
async def test_cancel_subscription_enqueues_canceled_event(db_session: AsyncSession) -> None:
    org = await create_organization(
        db_session,
        name="Cancel Org",
        external_id=f"ext-cancel-{uuid.uuid4().hex[:8]}",
        idempotency_key=f"idem-org-cancel-{uuid.uuid4().hex[:8]}",
    )
    product = await create_product(
        db_session,
        key=f"cancel_prod_{uuid.uuid4().hex[:6]}",
        name="Cancel",
    )
    plan = await create_plan(
        db_session,
        product_id=product.id,
        key=f"cancel_plan_{uuid.uuid4().hex[:6]}",
        billing_interval="month",
        trial_days=0,
    )
    await publish_plan(db_session, plan.id)

    now = datetime.now(UTC)
    subscription = Subscription(
        organization_id=org.id,
        plan_id=plan.id,
        status=SubscriptionStatus.active.value,
        current_period_start=now,
        current_period_end=now + timedelta(days=30),
    )
    db_session.add(subscription)
    await db_session.flush()

    await cancel_subscription(db_session, subscription, at_period_end=False)
    await db_session.commit()

    assert subscription.status == SubscriptionStatus.canceled.value
    assert await _count_outbox(db_session, event_type="subscription.canceled") == 1
