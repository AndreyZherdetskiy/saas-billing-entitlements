"""Unit tests: dunning attempt schedule and paused campaign skip."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from billing_platform.config import get_settings
from billing_platform.domain.models.dunning import (
    DunningAttempt,
    DunningCampaign,
    DunningCampaignStatus,
)
from billing_platform.domain.models.invoice import Invoice, InvoiceStatus
from billing_platform.domain.models.outbox_message import OutboxMessage
from billing_platform.domain.models.subscription import Subscription, SubscriptionStatus
from billing_platform.services.catalog import create_plan, create_product, publish_plan
from billing_platform.services.dunning import (
    pause_campaign,
    process_due_attempts,
    schedule_attempt_offsets_days,
    start_campaign,
)
from billing_platform.services.organizations import create_organization


def test_schedule_attempt_offsets_days() -> None:
    assert schedule_attempt_offsets_days() == (1, 3, 7)


@pytest.fixture
def dunning_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DUNNING_ENABLED", "true")
    get_settings.cache_clear()


async def _count_attempts(session: AsyncSession) -> int:
    result = await session.execute(select(func.count()).select_from(DunningAttempt))
    return int(result.scalar_one())


async def _count_outbox(session: AsyncSession) -> int:
    result = await session.execute(select(func.count()).select_from(OutboxMessage))
    return int(result.scalar_one())


@pytest.fixture
async def past_due_subscription(db_session: AsyncSession) -> Subscription:
    org = await create_organization(
        db_session,
        name="Schedule Org",
        external_id=f"ext-sched-{uuid.uuid4().hex[:8]}",
        idempotency_key=f"idem-org-sched-{uuid.uuid4().hex[:8]}",
    )
    product = await create_product(
        db_session,
        key=f"sched_prod_{uuid.uuid4().hex[:6]}",
        name="Schedule Product",
    )
    plan = await create_plan(
        db_session,
        product_id=product.id,
        key=f"sched_plan_{uuid.uuid4().hex[:6]}",
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
async def test_start_campaign_schedules_attempts_on_days_1_3_7(
    db_session: AsyncSession,
    past_due_subscription: Subscription,
    dunning_enabled: None,
) -> None:
    started_at = datetime(2026, 2, 16, 12, 0, tzinfo=UTC)
    campaign = await start_campaign(
        db_session,
        subscription_id=past_due_subscription.id,
        organization_id=past_due_subscription.organization_id,
        idempotency_key=f"dunning:sched:{uuid.uuid4().hex[:8]}",
        started_at=started_at,
    )
    assert campaign is not None

    result = await db_session.execute(
        select(DunningAttempt)
        .where(DunningAttempt.campaign_id == campaign.id)
        .order_by(DunningAttempt.attempt_no)
    )
    attempts = list(result.scalars().all())
    assert len(attempts) == 3
    assert [a.attempt_no for a in attempts] == [1, 2, 3]
    assert attempts[0].scheduled_at == started_at + timedelta(days=1)
    assert attempts[1].scheduled_at == started_at + timedelta(days=3)
    assert attempts[2].scheduled_at == started_at + timedelta(days=7)
    assert all(a.executed_at is None for a in attempts)


@pytest.mark.asyncio
async def test_process_due_attempts_skips_paused_campaign(
    db_session: AsyncSession,
    past_due_subscription: Subscription,
    dunning_enabled: None,
) -> None:
    started_at = datetime(2026, 2, 16, 12, 0, tzinfo=UTC)
    campaign = await start_campaign(
        db_session,
        subscription_id=past_due_subscription.id,
        organization_id=past_due_subscription.organization_id,
        idempotency_key=f"dunning:paused:{uuid.uuid4().hex[:8]}",
        started_at=started_at,
    )
    assert campaign is not None
    assert await _count_attempts(db_session) == 3

    await pause_campaign(
        db_session,
        campaign_public_id=campaign.id,
        organization_id=past_due_subscription.organization_id,
        actor_key_id=uuid.uuid4(),
    )

    outbox_before = await _count_outbox(db_session)
    now = started_at + timedelta(days=8)
    processed = await process_due_attempts(db_session, now=now)

    assert processed == 0
    assert await _count_outbox(db_session) == outbox_before

    result = await db_session.execute(
        select(DunningAttempt).where(DunningAttempt.campaign_id == campaign.id)
    )
    attempts = list(result.scalars().all())
    assert len(attempts) == 3
    assert all(a.executed_at is None for a in attempts)

    refreshed = await db_session.get(DunningCampaign, campaign.id)
    assert refreshed is not None
    assert refreshed.status == DunningCampaignStatus.PAUSED.value


@pytest.mark.asyncio
async def test_process_due_attempts_executes_active_campaign(
    db_session: AsyncSession,
    past_due_subscription: Subscription,
    dunning_enabled: None,
) -> None:
    started_at = datetime(2026, 2, 16, 12, 0, tzinfo=UTC)
    campaign = await start_campaign(
        db_session,
        subscription_id=past_due_subscription.id,
        organization_id=past_due_subscription.organization_id,
        idempotency_key=f"dunning:exec:{uuid.uuid4().hex[:8]}",
        started_at=started_at,
    )
    assert campaign is not None

    period_start = started_at
    period_end = started_at + timedelta(days=30)
    invoice = Invoice(
        organization_id=past_due_subscription.organization_id,
        subscription_id=past_due_subscription.id,
        status=InvoiceStatus.open.value,
        currency="USD",
        period_start=period_start,
        period_end=period_end,
        total_amount_cents=1500,
        external_invoice_id=f"in_{uuid.uuid4().hex[:12]}",
        idempotency_key=f"inv:{uuid.uuid4().hex[:8]}",
    )
    db_session.add(invoice)
    await db_session.flush()

    now = started_at + timedelta(days=1, hours=1)
    with patch(
        "billing_platform.services.dunning.MockStripeClient.retry_invoice_payment",
        new=AsyncMock(return_value={"id": invoice.external_invoice_id, "status": "open"}),
    ):
        processed = await process_due_attempts(db_session, now=now)

    assert processed == 1
    outbox_row = await db_session.scalar(
        select(OutboxMessage).where(OutboxMessage.event_type == "dunning.attempt_scheduled")
    )
    assert outbox_row is not None
    assert "organization_public_id" in outbox_row.payload
    assert "subscription_public_id" in outbox_row.payload
    assert "organization_id" not in outbox_row.payload
    assert "subscription_id" not in outbox_row.payload
