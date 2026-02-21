"""Unit tests: immediate subscription plan change with stub proration."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from billing_platform.domain.models.ledger import LedgerEntry, LedgerEntryType
from billing_platform.domain.models.outbox_message import OutboxMessage
from billing_platform.domain.models.subscription import Subscription, SubscriptionStatus
from billing_platform.services.catalog import (
    create_plan,
    create_price,
    create_product,
    publish_plan,
)
from billing_platform.services.organizations import create_organization
from billing_platform.services.plan_change import (
    PlanChangeNotAllowedError,
    change_plan,
)
from billing_platform.services.subscriptions import PlanNotPublishedError


async def _count_ledger_by_type(session: AsyncSession, entry_type: str) -> int:
    result = await session.execute(
        select(func.count()).select_from(LedgerEntry).where(LedgerEntry.entry_type == entry_type)
    )
    return int(result.scalar_one())


async def _count_outbox(session: AsyncSession, event_type: str) -> int:
    result = await session.execute(
        select(func.count())
        .select_from(OutboxMessage)
        .where(OutboxMessage.event_type == event_type)
    )
    return int(result.scalar_one())


@pytest.mark.asyncio
async def test_upgrade_changes_plan_id_and_posts_stub_proration(
    db_session: AsyncSession,
) -> None:
    org = await create_organization(
        db_session,
        name="Upgrade Org",
        external_id=f"ext-up-{uuid.uuid4().hex[:8]}",
        idempotency_key=f"idem-up-{uuid.uuid4().hex[:8]}",
    )
    product = await create_product(
        db_session,
        key=f"up_prod_{uuid.uuid4().hex[:6]}",
        name="Upgrade Product",
    )
    basic = await create_plan(
        db_session,
        product_id=product.id,
        key=f"basic_{uuid.uuid4().hex[:6]}",
        billing_interval="month",
        trial_days=0,
    )
    pro = await create_plan(
        db_session,
        product_id=product.id,
        key=f"pro_{uuid.uuid4().hex[:6]}",
        billing_interval="month",
        trial_days=0,
    )
    await create_price(
        db_session,
        plan_id=basic.id,
        unit_amount_cents=1000,
        pricing_model="flat",
    )
    await create_price(
        db_session,
        plan_id=pro.id,
        unit_amount_cents=5000,
        pricing_model="flat",
    )
    await publish_plan(db_session, basic.id)
    await publish_plan(db_session, pro.id)

    now = datetime.now(UTC)
    subscription = Subscription(
        organization_id=org.id,
        plan_id=basic.id,
        status=SubscriptionStatus.active.value,
        current_period_start=now,
        current_period_end=now + timedelta(days=30),
    )
    db_session.add(subscription)
    await db_session.flush()

    idem = f"idem-plan-change-{uuid.uuid4().hex[:8]}"
    updated = await change_plan(
        db_session,
        subscription=subscription,
        new_plan_id=pro.id,
        effective="immediate",
        idempotency_key=idem,
    )
    await db_session.commit()

    assert updated.plan_id == pro.id
    assert await _count_ledger_by_type(db_session, LedgerEntryType.proration_debit.value) == 1
    assert await _count_ledger_by_type(db_session, LedgerEntryType.proration_credit.value) == 0
    assert await _count_outbox(db_session, "subscription.plan_changed") == 1

    outbox_row = await db_session.scalar(
        select(OutboxMessage).where(OutboxMessage.event_type == "subscription.plan_changed")
    )
    assert outbox_row is not None
    assert outbox_row.payload["organization_public_id"] == str(org.public_id)
    assert outbox_row.payload["subscription_public_id"] == str(subscription.public_id)
    assert "organization_id" not in outbox_row.payload
    assert "subscription_id" not in outbox_row.payload

    debit = await db_session.scalar(
        select(LedgerEntry).where(LedgerEntry.entry_type == LedgerEntryType.proration_debit.value)
    )
    assert debit is not None
    assert debit.metadata_.get("stub") is True
    assert debit.metadata_.get("old_plan_id") == str(basic.id)
    assert debit.metadata_.get("new_plan_id") == str(pro.id)


@pytest.mark.asyncio
async def test_downgrade_posts_stub_proration_credit(db_session: AsyncSession) -> None:
    org = await create_organization(
        db_session,
        name="Downgrade Org",
        external_id=f"ext-down-{uuid.uuid4().hex[:8]}",
        idempotency_key=f"idem-down-{uuid.uuid4().hex[:8]}",
    )
    product = await create_product(
        db_session,
        key=f"down_prod_{uuid.uuid4().hex[:6]}",
        name="Downgrade Product",
    )
    basic = await create_plan(
        db_session,
        product_id=product.id,
        key=f"basic_d_{uuid.uuid4().hex[:6]}",
        billing_interval="month",
        trial_days=0,
    )
    pro = await create_plan(
        db_session,
        product_id=product.id,
        key=f"pro_d_{uuid.uuid4().hex[:6]}",
        billing_interval="month",
        trial_days=0,
    )
    await create_price(
        db_session,
        plan_id=basic.id,
        unit_amount_cents=1000,
        pricing_model="flat",
    )
    await create_price(
        db_session,
        plan_id=pro.id,
        unit_amount_cents=5000,
        pricing_model="flat",
    )
    await publish_plan(db_session, basic.id)
    await publish_plan(db_session, pro.id)

    now = datetime.now(UTC)
    subscription = Subscription(
        organization_id=org.id,
        plan_id=pro.id,
        status=SubscriptionStatus.active.value,
        current_period_start=now,
        current_period_end=now + timedelta(days=30),
    )
    db_session.add(subscription)
    await db_session.flush()

    await change_plan(
        db_session,
        subscription=subscription,
        new_plan_id=basic.id,
        effective="immediate",
        idempotency_key=f"idem-down-change-{uuid.uuid4().hex[:8]}",
    )
    await db_session.commit()

    assert subscription.plan_id == basic.id
    assert await _count_ledger_by_type(db_session, LedgerEntryType.proration_credit.value) == 1
    assert await _count_ledger_by_type(db_session, LedgerEntryType.proration_debit.value) == 0


@pytest.mark.asyncio
async def test_change_plan_rejects_canceled_subscription(db_session: AsyncSession) -> None:
    org = await create_organization(
        db_session,
        name="Canceled Org",
        external_id=f"ext-canceled-{uuid.uuid4().hex[:8]}",
        idempotency_key=f"idem-canceled-{uuid.uuid4().hex[:8]}",
    )
    product = await create_product(
        db_session,
        key=f"cancel_prod_{uuid.uuid4().hex[:6]}",
        name="Canceled Product",
    )
    basic = await create_plan(
        db_session,
        product_id=product.id,
        key=f"basic_c_{uuid.uuid4().hex[:6]}",
        billing_interval="month",
        trial_days=0,
    )
    pro = await create_plan(
        db_session,
        product_id=product.id,
        key=f"pro_c_{uuid.uuid4().hex[:6]}",
        billing_interval="month",
        trial_days=0,
    )
    await publish_plan(db_session, basic.id)
    await publish_plan(db_session, pro.id)

    now = datetime.now(UTC)
    subscription = Subscription(
        organization_id=org.id,
        plan_id=basic.id,
        status=SubscriptionStatus.canceled.value,
        current_period_start=now,
        current_period_end=now + timedelta(days=30),
        canceled_at=now,
    )
    db_session.add(subscription)
    await db_session.flush()

    with pytest.raises(PlanChangeNotAllowedError):
        await change_plan(
            db_session,
            subscription=subscription,
            new_plan_id=pro.id,
            effective="immediate",
            idempotency_key=f"idem-canceled-change-{uuid.uuid4().hex[:8]}",
        )


@pytest.mark.asyncio
async def test_change_plan_rejects_unpublished_target_plan(db_session: AsyncSession) -> None:
    org = await create_organization(
        db_session,
        name="Unpub Target Org",
        external_id=f"ext-unpub-t-{uuid.uuid4().hex[:8]}",
        idempotency_key=f"idem-unpub-t-{uuid.uuid4().hex[:8]}",
    )
    product = await create_product(
        db_session,
        key=f"unpub_t_prod_{uuid.uuid4().hex[:6]}",
        name="Unpub Target",
    )
    basic = await create_plan(
        db_session,
        product_id=product.id,
        key=f"basic_u_{uuid.uuid4().hex[:6]}",
        billing_interval="month",
        trial_days=0,
    )
    draft = await create_plan(
        db_session,
        product_id=product.id,
        key=f"draft_u_{uuid.uuid4().hex[:6]}",
        billing_interval="month",
        trial_days=0,
    )
    await publish_plan(db_session, basic.id)

    now = datetime.now(UTC)
    subscription = Subscription(
        organization_id=org.id,
        plan_id=basic.id,
        status=SubscriptionStatus.active.value,
        current_period_start=now,
        current_period_end=now + timedelta(days=30),
    )
    db_session.add(subscription)
    await db_session.flush()

    with pytest.raises(PlanNotPublishedError):
        await change_plan(
            db_session,
            subscription=subscription,
            new_plan_id=draft.id,
            effective="immediate",
            idempotency_key=f"idem-unpub-change-{uuid.uuid4().hex[:8]}",
        )
