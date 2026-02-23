"""Webhook ingestion (persist-first). HMAC-only auth — no API-key/Redis rate limit."""

from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncSession

from billing_platform.api.openapi_docs import BAD_REQUEST_RESPONSE
from billing_platform.config import Settings, get_settings
from billing_platform.db import get_session
from billing_platform.integrations.mock_stripe.signature import (
    InvalidWebhookSignature,
    verify_stripe_signature,
)
from billing_platform.integrations.redis_cache import get_redis_client
from billing_platform.logging import get_logger
from billing_platform.services.entitlements import bump_entitlement_version
from billing_platform.services.webhook_processor import process_webhook
from billing_platform.services.webhooks import persist_webhook

logger = get_logger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post(
    "/mock-stripe",
    status_code=status.HTTP_200_OK,
    summary="Ingest mock Stripe webhook",
    description=(
        "Accepts a Stripe-compatible webhook payload, verifies HMAC via Stripe-Signature header, "
        "and persists the event for idempotent processing. No API key — HMAC webhook auth only. "
        "Bumps entitlement cache for affected organizations after processing."
    ),
    responses=BAD_REQUEST_RESPONSE,
)
async def ingest_mock_stripe_webhook(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    stripe_signature: Annotated[str | None, Header(alias="Stripe-Signature")] = None,
) -> dict[str, str]:
    """Verify Stripe-compatible signature and persist webhook (idempotent)."""
    if stripe_signature is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="missing Stripe-Signature header",
        )

    raw_body = await request.body()
    try:
        verify_stripe_signature(
            raw_body,
            stripe_signature,
            secret=settings.mock_stripe_webhook_secret,
            previous_secret=settings.mock_stripe_webhook_secret_previous,
            tolerance_seconds=settings.webhook_timestamp_tolerance_seconds,
        )
    except InvalidWebhookSignature as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid JSON payload",
        ) from exc

    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="payload must be a JSON object",
        )

    provider_event_id = payload.get("id")
    event_type = payload.get("type")
    if not provider_event_id or not event_type:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="payload must include id and type",
        )

    webhook = await persist_webhook(
        session,
        provider_event_id=str(provider_event_id),
        event_type=str(event_type),
        payload=payload,
    )
    orgs_to_invalidate: set[int] = set()
    if webhook is not None:
        orgs_to_invalidate = await process_webhook(session, webhook.id)
    await session.commit()

    if orgs_to_invalidate:
        try:
            redis = await get_redis_client()
            for organization_id in orgs_to_invalidate:
                await bump_entitlement_version(redis, organization_id=organization_id)
        except RedisError as exc:
            for organization_id in orgs_to_invalidate:
                logger.warning(
                    "entitlement_cache_bump_failed",
                    organization_id=organization_id,
                    error=str(exc),
                )

    return {"status": "ok"}
