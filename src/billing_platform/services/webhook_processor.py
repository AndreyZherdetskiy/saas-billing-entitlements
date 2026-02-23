"""Idempotent webhook event processor."""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from billing_platform.config import get_settings
from billing_platform.domain.models.ledger import LedgerEntryType
from billing_platform.domain.models.organization import Organization
from billing_platform.domain.models.plan import Plan
from billing_platform.domain.models.subscription import Subscription, SubscriptionStatus
from billing_platform.domain.models.webhook_event import WebhookEvent, WebhookEventStatus
from billing_platform.domain.state_machines.subscription import IllegalTransition, transition
from billing_platform.observability.metrics import record_webhook_processing_duration_seconds
from billing_platform.services.dunning import start_campaign
from billing_platform.services.grace import compute_grace_until
from billing_platform.services.ledger import LedgerService
from billing_platform.services.outbox_hooks import enqueue_outbox


class WebhookProcessingError(Exception):
    """Unrecoverable webhook processing failure."""


_TERMINAL_STATUSES = frozenset(
    {
        WebhookEventStatus.PROCESSED,
        WebhookEventStatus.SKIPPED,
        WebhookEventStatus.FAILED,
    }
)


async def process_webhook(session: AsyncSession, webhook_id: uuid.UUID) -> set[int]:
    """Process a persisted webhook in the current transaction.

    Idempotent: already-processed webhooks are skipped without side effects.
    Poison payloads are marked failed with last_error (no crash loop).

    Returns organization IDs whose entitlement cache should be bumped after commit.
    """
    started = time.perf_counter()
    try:
        return await _process_webhook_inner(session, webhook_id)
    finally:
        record_webhook_processing_duration_seconds(time.perf_counter() - started)


async def _process_webhook_inner(session: AsyncSession, webhook_id: uuid.UUID) -> set[int]:
    result = await session.execute(
        select(WebhookEvent).where(WebhookEvent.id == webhook_id).with_for_update()
    )
    webhook = result.scalar_one_or_none()
    if webhook is None:
        return set()

    if webhook.status in _TERMINAL_STATUSES:
        return set()

    webhook.status = WebhookEventStatus.PROCESSING
    webhook.processing_attempts += 1

    orgs_to_invalidate: set[int] = set()
    try:
        orgs_to_invalidate = await _dispatch_event(session, webhook)
    except WebhookProcessingError as exc:
        webhook.status = WebhookEventStatus.FAILED
        webhook.last_error = str(exc)
        await session.flush()
        return set()
    except Exception as exc:  # noqa: BLE001 — poison guard (Gate D)
        webhook.status = WebhookEventStatus.FAILED
        webhook.last_error = str(exc)
        await session.flush()
        return set()

    if webhook.status == WebhookEventStatus.SKIPPED:
        await session.flush()
        return set()

    webhook.status = WebhookEventStatus.PROCESSED
    webhook.processed_at = datetime.now(UTC)
    await session.flush()
    return orgs_to_invalidate


async def _dispatch_event(session: AsyncSession, webhook: WebhookEvent) -> set[int]:
    handlers = {
        "invoice.paid": _handle_invoice_paid,
        "invoice.payment_failed": _handle_invoice_payment_failed,
    }
    handler = handlers.get(webhook.event_type)
    if handler is None:
        webhook.status = WebhookEventStatus.SKIPPED
        return set()
    return await handler(session, webhook)


def _extract_data_object(payload: dict[str, object]) -> dict[str, Any]:
    data = payload.get("data")
    if not isinstance(data, dict):
        raise WebhookProcessingError("payload missing data object")
    obj = data.get("object")
    if not isinstance(obj, dict):
        raise WebhookProcessingError("payload missing data.object")
    return obj


async def _find_subscription_by_external_id(
    session: AsyncSession,
    external_subscription_id: str,
) -> Subscription:
    result = await session.execute(
        select(Subscription).where(
            Subscription.external_subscription_id == external_subscription_id
        )
    )
    subscription = result.scalar_one_or_none()
    if subscription is None:
        raise WebhookProcessingError(
            f"subscription not found for external id {external_subscription_id}"
        )
    return subscription


async def _transition_subscription(
    session: AsyncSession,
    subscription: Subscription,
    *,
    new_status: SubscriptionStatus,
) -> None:
    current = SubscriptionStatus(subscription.status)
    try:
        subscription.status = transition(current, new_status).value
    except IllegalTransition as exc:
        raise WebhookProcessingError(str(exc)) from exc

    now = datetime.now(UTC)
    if new_status == SubscriptionStatus.past_due:
        subscription.past_due_entered_at = now
    elif new_status == SubscriptionStatus.active:
        subscription.past_due_entered_at = None

    await session.flush()


async def _load_plan(session: AsyncSession, plan_id: object) -> Plan:
    result = await session.execute(select(Plan).where(Plan.id == plan_id))
    plan = result.scalar_one_or_none()
    if plan is None:
        raise WebhookProcessingError(f"plan {plan_id} not found")
    return plan


async def _handle_invoice_paid(session: AsyncSession, webhook: WebhookEvent) -> set[int]:
    invoice = _extract_data_object(webhook.payload)
    external_sub_id = invoice.get("subscription")
    if not external_sub_id or not isinstance(external_sub_id, str):
        raise WebhookProcessingError("invoice.paid missing subscription id")

    subscription = await _find_subscription_by_external_id(session, external_sub_id)
    await _transition_subscription(
        session,
        subscription,
        new_status=SubscriptionStatus.active,
    )

    sub_public_id = str(subscription.public_id)
    org_result = await session.execute(
        select(Organization.public_id).where(Organization.id == subscription.organization_id)
    )
    org_public_id = str(org_result.scalar_one())
    invoice_id = str(invoice.get("id", ""))
    idem_base = f"webhook:{webhook.id}"

    await enqueue_outbox(
        session,
        aggregate_type="subscription",
        aggregate_id=sub_public_id,
        event_type="subscription.activated",
        payload={
            "subscription_public_id": sub_public_id,
            "organization_public_id": org_public_id,
            "webhook_id": str(webhook.id),
        },
        idempotency_key=f"{idem_base}:subscription.activated",
        partition_key=str(subscription.organization_id),
    )
    amount_paid = invoice.get("amount_paid")
    if not isinstance(amount_paid, int):
        amount_paid = 0
    currency_raw = invoice.get("currency")
    currency = currency_raw.upper() if isinstance(currency_raw, str) else "USD"

    await LedgerService.post(
        session,
        organization_id=subscription.organization_id,
        entry_type=LedgerEntryType.invoice_paid.value,
        amount_cents=amount_paid,
        currency=currency,
        idempotency_key=f"{idem_base}:ledger:invoice_paid",
        correlation_id=str(webhook.id),
        subscription_id=subscription.id,
        metadata={
            "invoice_external_id": invoice_id,
            "subscription_public_id": sub_public_id,
            "webhook_id": str(webhook.id),
        },
    )
    return {subscription.organization_id}


async def _handle_invoice_payment_failed(
    session: AsyncSession,
    webhook: WebhookEvent,
) -> set[int]:
    invoice = _extract_data_object(webhook.payload)
    external_sub_id = invoice.get("subscription")
    if not external_sub_id or not isinstance(external_sub_id, str):
        raise WebhookProcessingError("invoice.payment_failed missing subscription id")

    subscription = await _find_subscription_by_external_id(session, external_sub_id)
    await _transition_subscription(
        session,
        subscription,
        new_status=SubscriptionStatus.past_due,
    )

    plan = await _load_plan(session, subscription.plan_id)
    entered_at = subscription.past_due_entered_at
    grace_until = (
        compute_grace_until(
            past_due_entered_at=entered_at,
            grace_period_days=plan.grace_period_days,
        )
        if entered_at is not None
        else None
    )

    sub_public_id = str(subscription.public_id)
    org_result = await session.execute(
        select(Organization.public_id).where(Organization.id == subscription.organization_id)
    )
    org_public_id = str(org_result.scalar_one())
    idem_base = f"webhook:{webhook.id}"

    attempt_count = invoice.get("attempt_count")
    next_retry = invoice.get("next_payment_attempt")

    await enqueue_outbox(
        session,
        aggregate_type="subscription",
        aggregate_id=sub_public_id,
        event_type="subscription.payment_failed",
        payload={
            "subscription_public_id": sub_public_id,
            "organization_public_id": org_public_id,
            "webhook_id": str(webhook.id),
            "attempt_count": attempt_count if isinstance(attempt_count, int) else None,
            "next_retry": next_retry if isinstance(next_retry, str) else None,
        },
        idempotency_key=f"{idem_base}:subscription.payment_failed",
        partition_key=str(subscription.organization_id),
    )
    await enqueue_outbox(
        session,
        aggregate_type="subscription",
        aggregate_id=sub_public_id,
        event_type="subscription.past_due",
        payload={
            "subscription_public_id": sub_public_id,
            "organization_public_id": org_public_id,
            "webhook_id": str(webhook.id),
            "grace_until": (
                grace_until.isoformat().replace("+00:00", "Z") if grace_until is not None else None
            ),
        },
        idempotency_key=f"{idem_base}:subscription.past_due",
        partition_key=str(subscription.organization_id),
    )
    if get_settings().dunning_enabled:
        await start_campaign(
            session,
            subscription_id=subscription.id,
            organization_id=subscription.organization_id,
            idempotency_key=f"{idem_base}:dunning_campaign",
        )
    return {subscription.organization_id}
