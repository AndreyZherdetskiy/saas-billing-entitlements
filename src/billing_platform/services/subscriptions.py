from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from billing_platform.domain.models.organization import Organization
from billing_platform.domain.models.plan import Plan
from billing_platform.domain.models.subscription import Subscription, SubscriptionStatus
from billing_platform.domain.state_machines.subscription import transition
from billing_platform.services.outbox_hooks import enqueue_outbox


class SubscriptionError(Exception):
    """Base subscription service error."""


class PlanNotPublishedError(SubscriptionError):
    """Plan is not published."""


def _period_end(start: datetime, billing_interval: str) -> datetime:
    if billing_interval == "year":
        return start + timedelta(days=365)
    return start + timedelta(days=30)


def _initial_status(plan: Plan) -> SubscriptionStatus:
    if plan.trial_days is not None and plan.trial_days > 0:
        return SubscriptionStatus.trialing
    return SubscriptionStatus.incomplete


async def _find_existing_subscription(
    session: AsyncSession,
    *,
    idempotency_key: str,
) -> Subscription | None:
    result = await session.execute(
        select(Subscription).where(Subscription.idempotency_key == idempotency_key)
    )
    return result.scalar_one_or_none()


async def create_subscription(
    session: AsyncSession,
    *,
    organization_id: int,
    plan_id: uuid.UUID,
    idempotency_key: str,
    metadata: dict[str, object] | None = None,
) -> Subscription:
    """Create a subscription idempotently against a published plan."""
    existing = await _find_existing_subscription(session, idempotency_key=idempotency_key)
    if existing is not None:
        return existing

    plan_result = await session.execute(select(Plan).where(Plan.id == plan_id))
    plan = plan_result.scalar_one_or_none()
    if plan is None or plan.published_at is None:
        raise PlanNotPublishedError(f"plan {plan_id} is not published")

    now = datetime.now(UTC)
    status = _initial_status(plan)
    period_end = _period_end(now, plan.billing_interval)
    trial_days = plan.trial_days
    trial_end = (
        now + timedelta(days=trial_days)
        if status == SubscriptionStatus.trialing and trial_days is not None
        else None
    )

    subscription = Subscription(
        organization_id=organization_id,
        plan_id=plan_id,
        status=status.value,
        current_period_start=now,
        current_period_end=period_end,
        trial_end=trial_end,
        idempotency_key=idempotency_key,
        metadata_=metadata or {},
    )
    session.add(subscription)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        existing = await _find_existing_subscription(session, idempotency_key=idempotency_key)
        if existing is not None:
            return existing
        raise

    if status == SubscriptionStatus.trialing:
        sub_public_id = str(subscription.public_id)
        org_result = await session.execute(
            select(Organization.public_id).where(Organization.id == organization_id)
        )
        org_public_id = str(org_result.scalar_one())
        await enqueue_outbox(
            session,
            aggregate_type="subscription",
            aggregate_id=sub_public_id,
            event_type="subscription.trial_started",
            payload={
                "subscription_public_id": sub_public_id,
                "organization_public_id": org_public_id,
                "trial_end": trial_end.isoformat().replace("+00:00", "Z") if trial_end else None,
            },
            idempotency_key=f"subscription:{subscription.public_id}:trial_started",
            partition_key=str(organization_id),
        )

    return subscription


async def get_subscription_by_public_id(
    session: AsyncSession,
    public_id: object,
) -> Subscription | None:
    """Return a subscription by public_id or None."""
    if not isinstance(public_id, UUID):
        public_id = UUID(str(public_id))
    result = await session.execute(select(Subscription).where(Subscription.public_id == public_id))
    return result.scalar_one_or_none()


async def get_primary_subscription(
    session: AsyncSession,
    organization_id: int,
) -> Subscription | None:
    """Return the organization's primary subscription (most recently updated)."""
    result = await session.execute(
        select(Subscription)
        .where(Subscription.organization_id == organization_id)
        .order_by(Subscription.updated_at.desc(), Subscription.id.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def list_subscriptions_for_organization(
    session: AsyncSession,
    *,
    organization_id: int,
) -> list[Subscription]:
    """List subscriptions for an organization ordered by creation time."""
    result = await session.execute(
        select(Subscription)
        .where(Subscription.organization_id == organization_id)
        .order_by(Subscription.created_at)
    )
    return list(result.scalars().all())


async def cancel_subscription(
    session: AsyncSession,
    subscription: Subscription,
    *,
    at_period_end: bool,
) -> Subscription:
    """Cancel immediately or at period end."""
    if at_period_end:
        subscription.cancel_at_period_end = True
        await session.flush()
        return subscription

    current = SubscriptionStatus(subscription.status)
    new_status = transition(current, SubscriptionStatus.canceled)
    subscription.status = new_status.value
    subscription.canceled_at = datetime.now(UTC)
    subscription.cancel_at_period_end = False
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
        event_type="subscription.canceled",
        payload={
            "subscription_public_id": sub_public_id,
            "organization_public_id": org_public_id,
            "canceled_at": subscription.canceled_at.isoformat().replace("+00:00", "Z"),
        },
        idempotency_key=f"subscription:{subscription.public_id}:canceled",
        partition_key=str(subscription.organization_id),
    )

    return subscription
