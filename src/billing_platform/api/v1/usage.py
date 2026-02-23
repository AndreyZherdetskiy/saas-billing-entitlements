"""Usage event ingestion routes."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Path, status
from pydantic import AwareDatetime, BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from billing_platform.api.deps import assert_tenant_access, get_auth_context
from billing_platform.api.openapi_docs import (
    AUTH_RESPONSES,
    BAD_REQUEST_RESPONSE,
    FORBIDDEN_RESPONSE,
    NOT_FOUND_RESPONSE,
    USAGE_BATCH_EXAMPLES,
    merge_responses,
)
from billing_platform.db import get_read_session, get_session
from billing_platform.domain.models.api_key import ApiKeyRole
from billing_platform.services.api_keys import AuthContext
from billing_platform.services.organizations import get_organization_by_public_id
from billing_platform.services.subscriptions import get_primary_subscription
from billing_platform.services.usage import (
    UsageEventIn,
    ingest_usage_batch,
    list_usage_aggregates_for_period,
)

router = APIRouter(tags=["usage"])

_MAX_BATCH_SIZE = 1000
_ORG_PUBLIC_ID = Path(description="External UUIDv7 of the organization (not internal BIGINT id).")


class UsageEventRequest(BaseModel):
    feature_key: str = Field(
        min_length=1,
        max_length=255,
        description="Metered feature key (e.g. api_calls).",
    )
    quantity: int = Field(description="Usage quantity to record for this event.")
    idempotency_key: str = Field(
        min_length=1,
        max_length=255,
        description=(
            "Client-provided deduplication key, unique per organization "
            "(UNIQUE on organization_id + idempotency_key). "
            "Format is caller-defined (e.g. UUID or product event id); "
            "replays with the same key return the existing event without a second insert."
        ),
    )
    recorded_at: AwareDatetime | None = Field(
        default=None,
        description="UTC timestamp when usage occurred; defaults to ingestion time.",
    )


class UsageBatchRequest(BaseModel):
    organization_public_id: UUID = Field(
        description="External UUIDv7 of the organization (not the internal BIGINT id).",
    )
    events: list[UsageEventRequest] = Field(
        description="Usage events to ingest (max 1000 per request).",
    )


class UsageBatchResponse(BaseModel):
    accepted: int = Field(description="Count of newly accepted events in this batch.")
    duplicates: int = Field(description="Count of duplicate events skipped via idempotency_key.")
    usage_event_public_ids: list[str] = Field(
        description="External UUIDv7 ids of usage events in request order (including duplicates).",
    )


class UsageAggregateRow(BaseModel):
    feature_key: str = Field(description="Metered feature key.")
    period_start: AwareDatetime = Field(description="Start of the aggregation window (UTC).")
    period_end: AwareDatetime = Field(description="End of the aggregation window (UTC).")
    quantity: float = Field(description="Summed usage quantity in the window.")


class UsageAggregatesResponse(BaseModel):
    organization_public_id: UUID = Field(
        description="External UUIDv7 of the organization (not the internal BIGINT id).",
    )
    aggregates: list[UsageAggregateRow] = Field(
        description="Hourly usage aggregates for the current billing period.",
    )


@router.get(
    "/organizations/{organization_public_id}/usage",
    response_model=UsageAggregatesResponse,
    summary="Get organization usage aggregates",
    description=(
        "Returns summed hourly usage aggregates for the current billing period of the "
        "organization's primary subscription. Tenant keys may only read their own organization; "
        "platform_admin may read any. Returns an empty list when no primary subscription exists."
    ),
    responses=merge_responses(AUTH_RESPONSES, FORBIDDEN_RESPONSE, NOT_FOUND_RESPONSE),
)
async def get_organization_usage(
    organization_public_id: Annotated[UUID, _ORG_PUBLIC_ID],
    session: Annotated[AsyncSession, Depends(get_read_session)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> UsageAggregatesResponse:
    """Return summed hourly usage aggregates for the current billing period."""
    organization = await get_organization_by_public_id(session, organization_public_id)
    if organization is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="organization not found")
    assert_tenant_access(ctx, organization)

    subscription = await get_primary_subscription(session, organization.id)
    if subscription is None:
        return UsageAggregatesResponse(
            organization_public_id=organization.public_id,
            aggregates=[],
        )

    aggregates = await list_usage_aggregates_for_period(
        session,
        organization_id=organization.id,
        period_start=subscription.current_period_start,
        period_end=subscription.current_period_end,
    )
    return UsageAggregatesResponse(
        organization_public_id=organization.public_id,
        aggregates=[
            UsageAggregateRow(
                feature_key=row.feature_key,
                period_start=row.period_start,
                period_end=row.period_end,
                quantity=float(row.quantity),
            )
            for row in aggregates
        ],
    )


@router.post(
    "/usage/events/batch",
    response_model=UsageBatchResponse,
    summary="Ingest usage events batch",
    description=(
        "Records a batch of metered usage events for an organization (max 1000). "
        "Deduplicates by (organization, idempotency_key) so retries are safe. "
        "Requires product_service or platform_admin."
    ),
    responses=merge_responses(
        AUTH_RESPONSES,
        FORBIDDEN_RESPONSE,
        NOT_FOUND_RESPONSE,
        BAD_REQUEST_RESPONSE,
    ),
)
async def post_usage_events_batch(
    body: Annotated[
        UsageBatchRequest,
        Body(openapi_examples=USAGE_BATCH_EXAMPLES),
    ],
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> UsageBatchResponse:
    """Ingest usage events; ``idempotency_key`` is client-provided and unique per org."""
    if ctx.role not in (
        ApiKeyRole.PRODUCT_SERVICE.value,
        ApiKeyRole.PLATFORM_ADMIN.value,
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="only product_service or platform_admin may ingest usage",
        )

    if len(body.events) > _MAX_BATCH_SIZE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"usage batch cannot exceed {_MAX_BATCH_SIZE} events",
        )

    organization = await get_organization_by_public_id(session, body.organization_public_id)
    if organization is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="organization not found")
    assert_tenant_access(ctx, organization)

    result = await ingest_usage_batch(
        session,
        organization_id=organization.id,
        events=[
            UsageEventIn(
                feature_key=event.feature_key,
                quantity=event.quantity,
                idempotency_key=event.idempotency_key,
                recorded_at=event.recorded_at,
            )
            for event in body.events
        ],
    )
    await session.commit()
    return UsageBatchResponse(
        accepted=result.accepted,
        duplicates=result.duplicates,
        usage_event_public_ids=result.public_ids,
    )
