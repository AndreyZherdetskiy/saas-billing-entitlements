"""Grace period policy and expiry enforcement."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from billing_platform.domain.models.feature import Feature
from billing_platform.domain.models.ledger import LedgerEntryType
from billing_platform.domain.models.organization import Organization
from billing_platform.domain.models.plan import Plan
from billing_platform.domain.models.plan_feature import PlanFeature
from billing_platform.domain.models.subscription import Subscription, SubscriptionStatus
from billing_platform.domain.state_machines.subscription import IllegalTransition, transition
from billing_platform.services.ledger import LedgerService
from billing_platform.services.outbox_hooks import enqueue_outbox


def compute_grace_until(
    *,
    past_due_entered_at: datetime,
    grace_period_days: int,
) -> datetime:
    """Return the exclusive grace deadline (entered_at + grace_period_days)."""
    return past_due_entered_at + timedelta(days=grace_period_days)


def is_grace_active(
    *,
    status: str,
    grace_period_days: int,
    past_due_entered_at: datetime | None,
    now: datetime,
) -> bool:
    """Return whether subscription is within the grace window."""
    if status != SubscriptionStatus.past_due.value:
        return False
    if past_due_entered_at is None:
        return False
    grace_until = compute_grace_until(
        past_due_entered_at=past_due_entered_at,
        grace_period_days=grace_period_days,
    )
    return now < grace_until


async def _load_revoked_feature_keys(
    session: AsyncSession,
    plan_id: uuid.UUID,
) -> list[str]:
    result = await session.execute(
        select(Feature.key)
        .join(PlanFeature, PlanFeature.feature_id == Feature.id)
        .where(
            PlanFeature.plan_id == plan_id,
            PlanFeature.is_enabled.is_(True),
        )
    )
    return list(result.scalars().all())


async def _revoke_expired_subscription(
    session: AsyncSession,
    subscription: Subscription,
    plan: Plan,
    *,
    now: datetime,
) -> None:
    """Transition past_due subscription after grace expiry and emit revoke side effects."""
    current = SubscriptionStatus(subscription.status)
    if current != SubscriptionStatus.past_due:
        return

    target_status = (
        SubscriptionStatus.canceled
        if subscription.cancel_at_period_end
        else SubscriptionStatus.unpaid
    )
    try:
        subscription.status = transition(current, target_status).value
    except IllegalTransition as exc:
        raise RuntimeError(str(exc)) from exc

    if target_status == SubscriptionStatus.canceled:
        subscription.canceled_at = now
        subscription.cancel_at_period_end = False

    subscription.past_due_entered_at = None
    await session.flush()

    sub_public_id = str(subscription.public_id)
    org_result = await session.execute(
        select(Organization.public_id).where(Organization.id == subscription.organization_id)
    )
    org_public_id = str(org_result.scalar_one())
    idem_base = f"grace_expiry:{sub_public_id}"
    revoked_features = await _load_revoked_feature_keys(session, plan.id)

    await LedgerService.post(
        session,
        organization_id=subscription.organization_id,
        entry_type=LedgerEntryType.access_revoked_marker.value,
        amount_cents=0,
        currency="USD",
        idempotency_key=f"{idem_base}:ledger:access_revoked_marker",
        correlation_id=idem_base,
        subscription_id=subscription.id,
        metadata={
            "subscription_public_id": sub_public_id,
            "previous_status": SubscriptionStatus.past_due.value,
            "new_status": subscription.status,
            "revoked_features": revoked_features,
        },
    )
    await enqueue_outbox(
        session,
        aggregate_type="subscription",
        aggregate_id=sub_public_id,
        event_type="subscription.access_revoked",
        payload={
            "subscription_public_id": sub_public_id,
            "organization_public_id": org_public_id,
            "previous_status": SubscriptionStatus.past_due.value,
            "new_status": subscription.status,
            "revoked_features": revoked_features,
        },
        idempotency_key=f"{idem_base}:subscription.access_revoked",
        partition_key=str(subscription.organization_id),
    )


async def enforce_grace_expiry(session: AsyncSession, *, now: datetime) -> tuple[int, set[int]]:
    """Revoke access for past_due subscriptions whose grace window has ended.

    Returns (processed count, organization ids for entitlement cache bump after commit).
    """
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)

    result = await session.execute(
        select(Subscription, Plan)
        .join(Plan, Subscription.plan_id == Plan.id)
        .where(
            Subscription.status == SubscriptionStatus.past_due.value,
            Subscription.past_due_entered_at.is_not(None),
        )
        .with_for_update(skip_locked=True)
    )

    processed = 0
    org_ids_to_bump: set[int] = set()

    for subscription, plan in result.all():
        entered_at = subscription.past_due_entered_at
        if entered_at is None:
            continue
        if entered_at.tzinfo is None:
            entered_at = entered_at.replace(tzinfo=UTC)
        grace_until = compute_grace_until(
            past_due_entered_at=entered_at,
            grace_period_days=plan.grace_period_days,
        )
        if now < grace_until:
            continue

        await _revoke_expired_subscription(session, subscription, plan, now=now)
        processed += 1
        org_ids_to_bump.add(subscription.organization_id)

    return processed, org_ids_to_bump
