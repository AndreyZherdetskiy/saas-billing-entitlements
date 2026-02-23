"""Webhook ingestion service (persist-first)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from billing_platform.domain.ids import generate_uuidv7
from billing_platform.domain.models.webhook_event import WebhookEvent, WebhookEventStatus
from billing_platform.services.webhook_processor import process_webhook

PROVIDER_MOCK_STRIPE = "mock_stripe"

ReplayOutcome = Literal["replayed", "already_processed"]


@dataclass(frozen=True, slots=True)
class WebhookReplayResult:
    """Result of an admin replay attempt."""

    webhook_id: uuid.UUID
    outcome: ReplayOutcome
    status: WebhookEventStatus
    orgs_to_invalidate: set[int]


class WebhookNotFoundError(Exception):
    """Raised when replay targets a missing webhook row."""


async def persist_webhook(
    session: AsyncSession,
    *,
    provider_event_id: str,
    event_type: str,
    payload: dict[str, object],
    provider: str = PROVIDER_MOCK_STRIPE,
) -> WebhookEvent | None:
    """Insert webhook row; return None when provider_event_id already exists."""
    stmt = (
        insert(WebhookEvent)
        .values(
            id=generate_uuidv7(),
            provider=provider,
            provider_event_id=provider_event_id,
            event_type=event_type,
            payload=payload,
            status=WebhookEventStatus.RECEIVED,
            processing_attempts=0,
        )
        .on_conflict_do_nothing(index_elements=["provider_event_id"])
        .returning(WebhookEvent)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def replay_webhook(session: AsyncSession, webhook_id: uuid.UUID) -> WebhookReplayResult:
    """Reset failed/stuck webhooks and re-run ``process_webhook`` idempotently.

    Terminal success states (``processed``, ``skipped``) are left untouched so
    ledger/outbox idempotency keys are not exercised twice.
    """
    result = await session.execute(
        select(WebhookEvent).where(WebhookEvent.id == webhook_id).with_for_update()
    )
    webhook = result.scalar_one_or_none()
    if webhook is None:
        raise WebhookNotFoundError(str(webhook_id))

    if webhook.status in (
        WebhookEventStatus.PROCESSED,
        WebhookEventStatus.SKIPPED,
    ):
        return WebhookReplayResult(
            webhook_id=webhook.id,
            outcome="already_processed",
            status=webhook.status,
            orgs_to_invalidate=set(),
        )

    if webhook.status in (
        WebhookEventStatus.FAILED,
        WebhookEventStatus.PROCESSING,
    ):
        webhook.status = WebhookEventStatus.RECEIVED
        webhook.last_error = None
        await session.flush()

    orgs_to_invalidate = await process_webhook(session, webhook.id)
    await session.refresh(webhook)
    return WebhookReplayResult(
        webhook_id=webhook.id,
        outcome="replayed",
        status=webhook.status,
        orgs_to_invalidate=orgs_to_invalidate,
    )
