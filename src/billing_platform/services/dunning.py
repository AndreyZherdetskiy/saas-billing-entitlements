"""Dunning campaign orchestration (ADR-008)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from billing_platform.config import get_settings
from billing_platform.domain.models.dunning import (
    DunningAttempt,
    DunningAttemptResult,
    DunningCampaign,
    DunningCampaignStatus,
)
from billing_platform.domain.models.invoice import Invoice, InvoiceStatus
from billing_platform.domain.models.organization import Organization
from billing_platform.domain.models.plan import Plan
from billing_platform.domain.models.subscription import Subscription
from billing_platform.integrations.mock_stripe.client import MockStripeClient
from billing_platform.integrations.payment_provider import PaymentProviderPort
from billing_platform.observability.metrics import record_dunning_campaigns_active
from billing_platform.services.grace import compute_grace_until
from billing_platform.services.outbox_hooks import enqueue_outbox


def schedule_attempt_offsets_days() -> tuple[int, int, int]:
    """Return day offsets (from campaign start) for dunning attempts."""
    return (1, 3, 7)


async def _refresh_dunning_active_metric(session: AsyncSession) -> None:
    active = int(
        (
            await session.execute(
                select(func.count())
                .select_from(DunningCampaign)
                .where(DunningCampaign.status == DunningCampaignStatus.ACTIVE.value)
            )
        ).scalar_one()
    )
    record_dunning_campaigns_active(active)


async def _find_campaign_by_idempotency_key(
    session: AsyncSession,
    idempotency_key: str,
) -> DunningCampaign | None:
    result = await session.execute(
        select(DunningCampaign).where(DunningCampaign.idempotency_key == idempotency_key)
    )
    return result.scalar_one_or_none()


async def _find_active_campaign_for_subscription(
    session: AsyncSession,
    subscription_id: int,
) -> DunningCampaign | None:
    result = await session.execute(
        select(DunningCampaign).where(
            DunningCampaign.subscription_id == subscription_id,
            DunningCampaign.status == DunningCampaignStatus.ACTIVE.value,
        )
    )
    return result.scalar_one_or_none()


async def _get_campaign_by_id(
    session: AsyncSession,
    campaign_public_id: uuid.UUID,
    *,
    organization_id: int,
) -> DunningCampaign | None:
    result = await session.execute(
        select(DunningCampaign).where(
            DunningCampaign.id == campaign_public_id,
            DunningCampaign.organization_id == organization_id,
        )
    )
    return result.scalar_one_or_none()


def _append_campaign_audit(
    campaign: DunningCampaign,
    *,
    action: str,
    actor_key_id: uuid.UUID,
) -> None:
    snapshot = dict(campaign.policy_snapshot)
    history_raw = snapshot.get("operator_history")
    history: list[dict[str, str]] = list(history_raw) if isinstance(history_raw, list) else []
    history.append(
        {
            "action": action,
            "actor_key_id": str(actor_key_id),
            "at": datetime.now(UTC).isoformat(),
        }
    )
    snapshot["operator_history"] = history
    campaign.policy_snapshot = snapshot


async def schedule_campaign_attempts(
    session: AsyncSession,
    campaign: DunningCampaign,
) -> list[DunningAttempt]:
    """Create attempt rows for days 1/3/7 from campaign.started_at."""
    offsets = schedule_attempt_offsets_days()
    attempts: list[DunningAttempt] = []
    for attempt_no, day_offset in enumerate(offsets, start=1):
        attempt = DunningAttempt(
            campaign_id=campaign.id,
            attempt_no=attempt_no,
            scheduled_at=campaign.started_at + timedelta(days=day_offset),
            idempotency_key=f"{campaign.id}:{attempt_no}",
        )
        session.add(attempt)
        attempts.append(attempt)
    await session.flush()
    return attempts


async def start_campaign(
    session: AsyncSession,
    *,
    subscription_id: int,
    organization_id: int,
    idempotency_key: str,
    started_at: datetime | None = None,
) -> DunningCampaign | None:
    """Start a dunning campaign idempotently when dunning is enabled.

    Returns None when ``DUNNING_ENABLED`` is false (stage-1 no-op).
    """
    if not get_settings().dunning_enabled:
        return None

    existing = await _find_campaign_by_idempotency_key(session, idempotency_key)
    if existing is not None:
        return existing

    active = await _find_active_campaign_for_subscription(session, subscription_id)
    if active is not None:
        return active

    sub_result = await session.execute(
        select(Subscription, Plan)
        .join(Plan, Subscription.plan_id == Plan.id)
        .where(
            Subscription.id == subscription_id,
            Subscription.organization_id == organization_id,
        )
    )
    row = sub_result.one_or_none()
    if row is None:
        raise ValueError(
            f"subscription {subscription_id} not found for organization {organization_id}"
        )
    subscription, plan = row

    grace_until = None
    entered_at = subscription.past_due_entered_at
    if entered_at is not None:
        grace_until = compute_grace_until(
            past_due_entered_at=entered_at,
            grace_period_days=plan.grace_period_days,
        )

    campaign_started_at = started_at or datetime.now(UTC)
    campaign = DunningCampaign(
        subscription_id=subscription_id,
        organization_id=organization_id,
        status=DunningCampaignStatus.ACTIVE.value,
        grace_until=grace_until,
        policy_snapshot=dict(plan.dunning_policy),
        idempotency_key=idempotency_key,
        started_at=campaign_started_at,
    )
    try:
        async with session.begin_nested():
            session.add(campaign)
            await session.flush()
            await schedule_campaign_attempts(session, campaign)
    except IntegrityError:
        existing = await _find_campaign_by_idempotency_key(session, idempotency_key)
        if existing is not None:
            return existing
        active = await _find_active_campaign_for_subscription(session, subscription_id)
        if active is not None:
            return active
        raise
    await _refresh_dunning_active_metric(session)
    return campaign


async def list_campaigns(
    session: AsyncSession,
    *,
    organization_id: int | None = None,
    subscription_id: int | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[DunningCampaign]:
    """List dunning campaigns ordered by most recently started."""
    statement = select(DunningCampaign).order_by(DunningCampaign.started_at.desc())
    if organization_id is not None:
        statement = statement.where(DunningCampaign.organization_id == organization_id)
    if subscription_id is not None:
        statement = statement.where(DunningCampaign.subscription_id == subscription_id)
    statement = statement.limit(limit).offset(offset)
    result = await session.execute(statement)
    return list(result.scalars().all())


async def pause_campaign(
    session: AsyncSession,
    *,
    campaign_public_id: uuid.UUID,
    organization_id: int,
    actor_key_id: uuid.UUID,
) -> DunningCampaign:
    """Pause an active campaign without deleting scheduled attempts."""
    campaign = await _get_campaign_by_id(
        session,
        campaign_public_id,
        organization_id=organization_id,
    )
    if campaign is None:
        raise ValueError("dunning campaign not found")
    if campaign.status != DunningCampaignStatus.ACTIVE.value:
        raise ValueError(f"cannot pause campaign in status {campaign.status}")

    campaign.status = DunningCampaignStatus.PAUSED.value
    _append_campaign_audit(campaign, action="pause", actor_key_id=actor_key_id)
    await session.flush()
    await _refresh_dunning_active_metric(session)
    return campaign


async def resume_campaign(
    session: AsyncSession,
    *,
    campaign_public_id: uuid.UUID,
    organization_id: int,
    actor_key_id: uuid.UUID,
) -> DunningCampaign:
    """Resume a paused campaign."""
    campaign = await _get_campaign_by_id(
        session,
        campaign_public_id,
        organization_id=organization_id,
    )
    if campaign is None:
        raise ValueError("dunning campaign not found")
    if campaign.status != DunningCampaignStatus.PAUSED.value:
        raise ValueError(f"cannot resume campaign in status {campaign.status}")

    other_active = await _find_active_campaign_for_subscription(session, campaign.subscription_id)
    if other_active is not None and other_active.id != campaign.id:
        raise ValueError("another active dunning campaign exists for subscription")

    campaign.status = DunningCampaignStatus.ACTIVE.value
    _append_campaign_audit(campaign, action="resume", actor_key_id=actor_key_id)
    await session.flush()
    await _refresh_dunning_active_metric(session)
    return campaign


async def _find_open_invoice_for_subscription(
    session: AsyncSession,
    subscription_id: int,
) -> Invoice | None:
    result = await session.execute(
        select(Invoice)
        .where(
            Invoice.subscription_id == subscription_id,
            Invoice.status == InvoiceStatus.open.value,
            Invoice.external_invoice_id.is_not(None),
        )
        .order_by(Invoice.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


def _event_type_for_attempt(attempt_no: int, *, retry_succeeded: bool) -> str:
    if attempt_no == 1:
        return "dunning.attempt_scheduled"
    if attempt_no == 3:
        return "dunning.final_notice"
    if retry_succeeded:
        return "dunning.attempt_succeeded"
    return "dunning.attempt_failed"


async def _execute_attempt(
    session: AsyncSession,
    attempt: DunningAttempt,
    campaign: DunningCampaign,
    *,
    now: datetime,
    payment_provider: PaymentProviderPort | None = None,
) -> int:
    """Run one due attempt: mock Stripe retry + outbox event (no ledger mutation)."""
    invoice = await _find_open_invoice_for_subscription(session, campaign.subscription_id)
    retry_succeeded = False
    external_charge_id: str | None = None

    if invoice is not None and invoice.external_invoice_id is not None:
        client = payment_provider or MockStripeClient()
        retry_result = await client.retry_invoice_payment(invoice_id=invoice.external_invoice_id)
        status = retry_result.get("status")
        retry_succeeded = status == "paid"
        charge = retry_result.get("latest_charge")
        if isinstance(charge, str):
            external_charge_id = charge

    attempt.executed_at = now
    attempt.result = (
        DunningAttemptResult.SUCCEEDED.value
        if retry_succeeded
        else DunningAttemptResult.FAILED.value
    )
    attempt.external_charge_id = external_charge_id
    await session.flush()

    event_type = _event_type_for_attempt(attempt.attempt_no, retry_succeeded=retry_succeeded)
    campaign_public_id = str(campaign.id)
    org_result = await session.execute(
        select(Organization.public_id).where(Organization.id == campaign.organization_id)
    )
    org_public_id = str(org_result.scalar_one())
    sub_result = await session.execute(
        select(Subscription.public_id).where(Subscription.id == campaign.subscription_id)
    )
    sub_public_id = str(sub_result.scalar_one())
    await enqueue_outbox(
        session,
        aggregate_type="dunning_campaign",
        aggregate_id=campaign_public_id,
        event_type=event_type,
        payload={
            "campaign_id": campaign_public_id,
            "organization_public_id": org_public_id,
            "subscription_public_id": sub_public_id,
            "attempt_no": attempt.attempt_no,
            "result": attempt.result,
            "external_charge_id": external_charge_id,
        },
        idempotency_key=f"dunning:{campaign.id}:attempt:{attempt.attempt_no}",
        partition_key=str(campaign.organization_id),
    )

    if retry_succeeded:
        campaign.status = DunningCampaignStatus.COMPLETED.value
    elif attempt.attempt_no == 3:
        campaign.status = DunningCampaignStatus.EXHAUSTED.value

    await session.flush()
    return 1


async def process_due_attempts(
    session: AsyncSession,
    *,
    now: datetime,
    payment_provider: PaymentProviderPort | None = None,
) -> int:
    """Execute due attempts for active campaigns; skip paused campaigns."""
    if not get_settings().dunning_enabled:
        return 0

    result = await session.execute(
        select(DunningAttempt)
        .join(DunningCampaign, DunningAttempt.campaign_id == DunningCampaign.id)
        .where(
            DunningCampaign.status == DunningCampaignStatus.ACTIVE.value,
            DunningAttempt.executed_at.is_(None),
            DunningAttempt.scheduled_at <= now,
        )
        .options(selectinload(DunningAttempt.campaign))
        .order_by(DunningAttempt.scheduled_at, DunningAttempt.attempt_no)
        .with_for_update(skip_locked=True)
    )
    attempts = list(result.scalars().unique().all())

    processed = 0
    for attempt in attempts:
        campaign = attempt.campaign
        processed += await _execute_attempt(
            session,
            attempt,
            campaign,
            now=now,
            payment_provider=payment_provider,
        )
    await _refresh_dunning_active_metric(session)
    return processed
