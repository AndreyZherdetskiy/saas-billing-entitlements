"""Immediate subscription plan change with stub proration."""

from __future__ import annotations

import uuid
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from billing_platform.domain.models.ledger import LedgerEntryType
from billing_platform.domain.models.organization import Organization
from billing_platform.domain.models.outbox_message import OutboxMessage
from billing_platform.domain.models.plan import Plan
from billing_platform.domain.models.price import Price
from billing_platform.domain.models.subscription import Subscription, SubscriptionStatus
from billing_platform.services.ledger import LedgerService
from billing_platform.services.outbox_hooks import enqueue_outbox
from billing_platform.services.subscriptions import PlanNotPublishedError

PLAN_CHANGED_EVENT = "subscription.plan_changed"
STUB_PRORATION_CENTS = 1

FORBIDDEN_STATUSES = frozenset(
    {
        SubscriptionStatus.canceled,
        SubscriptionStatus.unpaid,
    }
)


class PlanChangeError(Exception):
    """Base plan change service error."""


class PlanChangeNotAllowedError(PlanChangeError):
    """Subscription status does not allow plan change."""


async def _find_plan_changed_outbox(
    session: AsyncSession,
    idempotency_key: str,
) -> OutboxMessage | None:
    outbox_idempotency_key = f"plan_change:{idempotency_key}:plan_changed"
    result = await session.execute(
        select(OutboxMessage).where(OutboxMessage.idempotency_key == outbox_idempotency_key)
    )
    return result.scalar_one_or_none()


async def _plan_flat_price_cents(session: AsyncSession, plan_id: uuid.UUID) -> int:
    result = await session.execute(
        select(func.coalesce(func.sum(Price.unit_amount_cents), 0)).where(
            Price.plan_id == plan_id,
            Price.is_active.is_(True),
            Price.pricing_model == "flat",
        )
    )
    return int(result.scalar_one())


async def change_plan(
    session: AsyncSession,
    *,
    subscription: Subscription,
    new_plan_id: uuid.UUID,
    effective: Literal["immediate"],
    idempotency_key: str,
) -> Subscription:
    """Change subscription to a published plan immediately with stub proration ledger rows."""
    if effective != "immediate":
        raise PlanChangeError(f"unsupported effective policy: {effective}")

    existing_outbox = await _find_plan_changed_outbox(session, idempotency_key)
    if existing_outbox is not None:
        return subscription

    current_status = SubscriptionStatus(subscription.status)
    if current_status in FORBIDDEN_STATUSES:
        raise PlanChangeNotAllowedError(
            f"plan change not allowed for subscription status {current_status.value}"
        )

    if subscription.plan_id == new_plan_id:
        return subscription

    plan_result = await session.execute(select(Plan).where(Plan.id == new_plan_id))
    new_plan = plan_result.scalar_one_or_none()
    if new_plan is None or new_plan.published_at is None:
        raise PlanNotPublishedError(f"plan {new_plan_id} is not published")

    old_plan_id = subscription.plan_id
    correlation_id = f"plan_change:{idempotency_key}"

    old_price_cents = await _plan_flat_price_cents(session, old_plan_id)
    new_price_cents = await _plan_flat_price_cents(session, new_plan_id)

    proration_metadata: dict[str, object] = {
        "old_plan_id": str(old_plan_id),
        "new_plan_id": str(new_plan_id),
        "stub": True,
    }

    if new_price_cents > old_price_cents:
        await LedgerService.post(
            session,
            organization_id=subscription.organization_id,
            subscription_id=subscription.id,
            entry_type=LedgerEntryType.proration_debit.value,
            amount_cents=STUB_PRORATION_CENTS,
            currency="USD",
            idempotency_key=f"plan_change:{idempotency_key}:proration_debit",
            correlation_id=correlation_id,
            metadata=proration_metadata,
        )
    elif new_price_cents < old_price_cents:
        await LedgerService.post(
            session,
            organization_id=subscription.organization_id,
            subscription_id=subscription.id,
            entry_type=LedgerEntryType.proration_credit.value,
            amount_cents=-STUB_PRORATION_CENTS,
            currency="USD",
            idempotency_key=f"plan_change:{idempotency_key}:proration_credit",
            correlation_id=correlation_id,
            metadata=proration_metadata,
        )

    subscription.plan_id = new_plan_id
    await session.flush()

    sub_public_id = str(subscription.public_id)
    org_result = await session.execute(
        select(Organization.public_id).where(Organization.id == subscription.organization_id)
    )
    org_public_id = str(org_result.scalar_one())
    await enqueue_outbox(
        session,
        aggregate_type="subscription",
        aggregate_id=sub_public_id,
        event_type=PLAN_CHANGED_EVENT,
        payload={
            "subscription_public_id": sub_public_id,
            "organization_public_id": org_public_id,
            "old_plan_id": str(old_plan_id),
            "new_plan_id": str(new_plan_id),
            "effective": effective,
            "proration_stub": True,
        },
        idempotency_key=f"plan_change:{idempotency_key}:plan_changed",
        partition_key=str(subscription.organization_id),
    )

    return subscription
