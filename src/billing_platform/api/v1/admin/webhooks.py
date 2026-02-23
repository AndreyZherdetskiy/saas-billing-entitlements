"""Admin webhook replay routes."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, status
from pydantic import BaseModel, Field
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncSession

from billing_platform.api.deps import require_platform_admin
from billing_platform.api.openapi_docs import (
    AUTH_RESPONSES,
    FORBIDDEN_RESPONSE,
    NOT_FOUND_RESPONSE,
    merge_responses,
)
from billing_platform.db import get_session
from billing_platform.integrations.redis_cache import get_redis_client
from billing_platform.logging import get_logger
from billing_platform.services.api_keys import AuthContext
from billing_platform.services.entitlements import bump_entitlement_version
from billing_platform.services.webhooks import WebhookNotFoundError, replay_webhook

router = APIRouter(prefix="/admin/webhooks", tags=["webhooks-admin"])
logger = get_logger(__name__)

_WEBHOOK_ID = Path(
    description="External UUIDv7 of the persisted webhook event (not internal BIGINT id).",
)


class WebhookReplayResponse(BaseModel):
    """Replay outcome for a persisted webhook event."""

    id: UUID = Field(description="External UUIDv7 of the webhook event (not internal BIGINT id).")
    replay_result: str = Field(description="Processor outcome for the replay attempt.")
    status: str = Field(description="Persisted webhook event status after replay.")


@router.post(
    "/{webhook_id}/replay",
    response_model=WebhookReplayResponse,
    summary="Replay webhook event",
    description=(
        "Reprocesses a previously persisted webhook event that failed or is stuck. "
        "Platform_admin only. Uses the same processor as ingestion and bumps entitlement "
        "cache for affected organizations on success."
    ),
    responses=merge_responses(AUTH_RESPONSES, FORBIDDEN_RESPONSE, NOT_FOUND_RESPONSE),
)
async def replay_webhook_event(
    webhook_id: Annotated[UUID, _WEBHOOK_ID],
    session: Annotated[AsyncSession, Depends(get_session)],
    _ctx: Annotated[AuthContext, Depends(require_platform_admin)],
) -> WebhookReplayResponse:
    """Replay a failed or stuck webhook using the same processor as ingestion."""
    try:
        result = await replay_webhook(session, webhook_id)
    except WebhookNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="webhook not found",
        ) from exc

    await session.commit()

    if result.orgs_to_invalidate:
        try:
            redis = await get_redis_client()
            for organization_id in result.orgs_to_invalidate:
                await bump_entitlement_version(redis, organization_id=organization_id)
        except RedisError as exc:
            for organization_id in result.orgs_to_invalidate:
                logger.warning(
                    "entitlement_cache_bump_failed",
                    organization_id=organization_id,
                    error=str(exc),
                )

    return WebhookReplayResponse(
        id=result.webhook_id,
        replay_result=result.outcome,
        status=result.status.value,
    )
