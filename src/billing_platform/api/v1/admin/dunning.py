"""Dunning admin HTTP routes."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from billing_platform.api.deps import assert_tenant_access, require_dunning_operator
from billing_platform.api.openapi_docs import (
    AUTH_RESPONSES,
    BAD_REQUEST_RESPONSE,
    CONFLICT_RESPONSE,
    FORBIDDEN_RESPONSE,
    NOT_FOUND_RESPONSE,
    merge_responses,
)
from billing_platform.db import get_session
from billing_platform.domain.models.api_key import ApiKey, ApiKeyRole
from billing_platform.domain.models.dunning import DunningCampaign
from billing_platform.domain.models.organization import Organization
from billing_platform.domain.models.subscription import Subscription
from billing_platform.services.api_keys import AuthContext
from billing_platform.services.dunning import list_campaigns, pause_campaign, resume_campaign
from billing_platform.services.organizations import get_organization_by_public_id
from billing_platform.services.subscriptions import get_subscription_by_public_id

router = APIRouter(prefix="/admin/dunning", tags=["dunning"])

_CAMPAIGN_ID = Path(
    description="External UUIDv7 of the dunning campaign (not internal BIGINT id).",
)
_ORG_PUBLIC_ID_QUERY = Query(
    description="Filter by organization external UUIDv7 (not internal BIGINT id).",
)
_SUB_PUBLIC_ID_QUERY = Query(
    description="Filter by subscription external UUIDv7 (not internal BIGINT id).",
)


class DunningCampaignResponse(BaseModel):
    """Public dunning campaign representation (no internal BIGINT ids)."""

    id: UUID = Field(
        description="External UUIDv7 of the dunning campaign (not internal BIGINT id).",
    )
    subscription_public_id: UUID = Field(
        description="External UUIDv7 of the subscription (not internal BIGINT id).",
    )
    organization_public_id: UUID = Field(
        description="External UUIDv7 of the organization (not internal BIGINT id).",
    )
    status: str = Field(description="Campaign status (e.g. active, paused).")
    grace_until: datetime | None = Field(
        default=None,
        description="UTC timestamp until grace period ends, if applicable.",
    )
    started_at: datetime = Field(description="UTC timestamp when the campaign started.")
    created_at: datetime = Field(description="UTC timestamp when the campaign was created.")


async def _load_organization_by_id(
    session: AsyncSession,
    organization_id: int,
) -> Organization | None:
    result = await session.execute(select(Organization).where(Organization.id == organization_id))
    return result.scalar_one_or_none()


async def _subscription_public_id(
    session: AsyncSession,
    subscription_id: int,
) -> UUID:
    result = await session.execute(
        select(Subscription.public_id).where(Subscription.id == subscription_id)
    )
    public_id = result.scalar_one_or_none()
    if public_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="subscription not found",
        )
    return public_id


async def _campaign_to_response(
    session: AsyncSession,
    campaign: DunningCampaign,
    organization: Organization,
) -> DunningCampaignResponse:
    return DunningCampaignResponse(
        id=campaign.id,
        subscription_public_id=await _subscription_public_id(session, campaign.subscription_id),
        organization_public_id=organization.public_id,
        status=campaign.status,
        grace_until=campaign.grace_until,
        started_at=campaign.started_at,
        created_at=campaign.created_at,
    )


async def _load_campaign_and_assert_tenant(
    session: AsyncSession,
    ctx: AuthContext,
    campaign_id: UUID,
) -> tuple[DunningCampaign, Organization]:
    result = await session.execute(
        select(DunningCampaign).where(DunningCampaign.id == campaign_id)
    )
    campaign = result.scalar_one_or_none()
    if campaign is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="dunning campaign not found",
        )
    organization = await _load_organization_by_id(session, campaign.organization_id)
    if organization is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="organization not found",
        )
    assert_tenant_access(ctx, organization)
    return campaign, organization


async def _resolve_actor_key_id(
    session: AsyncSession,
    ctx: AuthContext,
) -> UUID:
    result = await session.execute(
        select(ApiKey.id).where(ApiKey.key_prefix == ctx.key_prefix, ApiKey.revoked_at.is_(None))
    )
    actor_key_id = result.scalar_one_or_none()
    if actor_key_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="api key not found",
        )
    return actor_key_id


async def _campaigns_to_responses(
    session: AsyncSession,
    campaigns: list[DunningCampaign],
) -> list[DunningCampaignResponse]:
    if not campaigns:
        return []

    organization_ids = {campaign.organization_id for campaign in campaigns}
    org_result = await session.execute(
        select(Organization).where(Organization.id.in_(organization_ids))
    )
    organizations_by_id = {org.id: org for org in org_result.scalars().all()}

    subscription_ids = {campaign.subscription_id for campaign in campaigns}
    sub_result = await session.execute(
        select(Subscription.id, Subscription.public_id).where(
            Subscription.id.in_(subscription_ids)
        )
    )
    subscription_public_by_id = {row[0]: row[1] for row in sub_result.all()}

    responses: list[DunningCampaignResponse] = []
    for campaign in campaigns:
        organization = organizations_by_id.get(campaign.organization_id)
        subscription_public_id = subscription_public_by_id.get(campaign.subscription_id)
        if organization is None or subscription_public_id is None:
            continue
        responses.append(
            DunningCampaignResponse(
                id=campaign.id,
                subscription_public_id=subscription_public_id,
                organization_public_id=organization.public_id,
                status=campaign.status,
                grace_until=campaign.grace_until,
                started_at=campaign.started_at,
                created_at=campaign.created_at,
            )
        )
    return responses


async def _resolve_list_filters(
    session: AsyncSession,
    ctx: AuthContext,
    *,
    organization_public_id: UUID | None,
    subscription_public_id: UUID | None,
) -> tuple[int | None, int | None]:
    organization_id: int | None = None
    subscription_id: int | None = None

    if organization_public_id is not None:
        organization = await get_organization_by_public_id(session, organization_public_id)
        if organization is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="organization not found",
            )
        assert_tenant_access(ctx, organization)
        organization_id = organization.id

    if subscription_public_id is not None:
        subscription = await get_subscription_by_public_id(session, subscription_public_id)
        if subscription is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="subscription not found",
            )
        subscription_organization = await _load_organization_by_id(
            session,
            subscription.organization_id,
        )
        if subscription_organization is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="organization not found",
            )
        assert_tenant_access(ctx, subscription_organization)
        if organization_id is not None and organization_id != subscription.organization_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="subscription does not belong to organization",
            )
        subscription_id = subscription.id
        organization_id = subscription.organization_id

    if ctx.role != ApiKeyRole.PLATFORM_ADMIN.value:
        if ctx.organization_id is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="cross-tenant access denied",
            )
        if organization_id is not None and organization_id != ctx.organization_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="cross-tenant access denied",
            )
        if organization_id is None:
            organization_id = ctx.organization_id

    return organization_id, subscription_id


@router.get(
    "/campaigns",
    response_model=list[DunningCampaignResponse],
    summary="List dunning campaigns",
    description=(
        "Lists dunning campaigns with optional filters by organization or subscription. "
        "Requires dunning_operator or platform_admin. Tenant-scoped keys see only their "
        "organization. Paginated via limit (max 200) and offset."
    ),
    responses=merge_responses(
        AUTH_RESPONSES,
        FORBIDDEN_RESPONSE,
        NOT_FOUND_RESPONSE,
        BAD_REQUEST_RESPONSE,
    ),
)
async def list_dunning_campaigns(
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[AuthContext, Depends(require_dunning_operator)],
    organization_public_id: Annotated[UUID | None, _ORG_PUBLIC_ID_QUERY] = None,
    subscription_public_id: Annotated[UUID | None, _SUB_PUBLIC_ID_QUERY] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[DunningCampaignResponse]:
    """List dunning campaigns with optional organization or subscription filters."""
    organization_id, subscription_id = await _resolve_list_filters(
        session,
        ctx,
        organization_public_id=organization_public_id,
        subscription_public_id=subscription_public_id,
    )
    campaigns = await list_campaigns(
        session,
        organization_id=organization_id,
        subscription_id=subscription_id,
        limit=limit,
        offset=offset,
    )
    return await _campaigns_to_responses(session, campaigns)


@router.post(
    "/campaigns/{campaign_id}/pause",
    response_model=DunningCampaignResponse,
    summary="Pause dunning campaign",
    description=(
        "Pauses an active dunning campaign without deleting scheduled retry attempts. "
        "Requires dunning_operator or platform_admin. Tenant-scoped keys may pause only "
        "campaigns for their organization."
    ),
    responses=merge_responses(
        AUTH_RESPONSES,
        FORBIDDEN_RESPONSE,
        NOT_FOUND_RESPONSE,
        CONFLICT_RESPONSE,
    ),
)
async def pause_dunning_campaign(
    campaign_id: Annotated[UUID, _CAMPAIGN_ID],
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[AuthContext, Depends(require_dunning_operator)],
) -> DunningCampaignResponse:
    """Pause a dunning campaign without deleting scheduled attempts."""
    _campaign, organization = await _load_campaign_and_assert_tenant(session, ctx, campaign_id)
    actor_key_id = await _resolve_actor_key_id(session, ctx)
    try:
        campaign = await pause_campaign(
            session,
            campaign_public_id=campaign_id,
            organization_id=organization.id,
            actor_key_id=actor_key_id,
        )
    except ValueError as exc:
        detail = str(exc)
        code = status.HTTP_404_NOT_FOUND if "not found" in detail else status.HTTP_409_CONFLICT
        raise HTTPException(status_code=code, detail=detail) from exc
    await session.commit()
    return await _campaign_to_response(session, campaign, organization)


@router.post(
    "/campaigns/{campaign_id}/resume",
    response_model=DunningCampaignResponse,
    summary="Resume dunning campaign",
    description=(
        "Resumes a paused dunning campaign so scheduled retry attempts continue. "
        "Requires dunning_operator or platform_admin. Tenant-scoped keys may resume only "
        "campaigns for their organization."
    ),
    responses=merge_responses(
        AUTH_RESPONSES,
        FORBIDDEN_RESPONSE,
        NOT_FOUND_RESPONSE,
        CONFLICT_RESPONSE,
    ),
)
async def resume_dunning_campaign(
    campaign_id: Annotated[UUID, _CAMPAIGN_ID],
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[AuthContext, Depends(require_dunning_operator)],
) -> DunningCampaignResponse:
    """Resume a paused dunning campaign."""
    _campaign, organization = await _load_campaign_and_assert_tenant(session, ctx, campaign_id)
    actor_key_id = await _resolve_actor_key_id(session, ctx)
    try:
        campaign = await resume_campaign(
            session,
            campaign_public_id=campaign_id,
            organization_id=organization.id,
            actor_key_id=actor_key_id,
        )
    except ValueError as exc:
        detail = str(exc)
        code = status.HTTP_404_NOT_FOUND if "not found" in detail else status.HTTP_409_CONFLICT
        raise HTTPException(status_code=code, detail=detail) from exc
    await session.commit()
    return await _campaign_to_response(session, campaign, organization)
