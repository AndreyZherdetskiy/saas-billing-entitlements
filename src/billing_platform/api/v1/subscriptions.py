"""Subscription HTTP routes (API C)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Path, status
from pydantic import BaseModel, Field
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from billing_platform.api.deps import assert_tenant_access, get_auth_context
from billing_platform.api.openapi_docs import (
    AUTH_RESPONSES,
    BAD_REQUEST_RESPONSE,
    CONFLICT_RESPONSE,
    CREATE_SUBSCRIPTION_EXAMPLES,
    FORBIDDEN_RESPONSE,
    NOT_FOUND_RESPONSE,
    merge_responses,
)
from billing_platform.db import get_session
from billing_platform.domain.models.api_key import ApiKeyRole
from billing_platform.domain.models.organization import Organization
from billing_platform.domain.models.subscription import Subscription
from billing_platform.domain.state_machines.subscription import IllegalTransition
from billing_platform.integrations.redis_cache import get_redis_client
from billing_platform.logging import get_logger
from billing_platform.services.api_keys import AuthContext
from billing_platform.services.entitlements import bump_entitlement_version
from billing_platform.services.organizations import get_organization_by_public_id
from billing_platform.services.plan_change import PlanChangeNotAllowedError, change_plan
from billing_platform.services.subscriptions import (
    PlanNotPublishedError,
    cancel_subscription,
    create_subscription,
    get_subscription_by_public_id,
    list_subscriptions_for_organization,
)

logger = get_logger(__name__)

router = APIRouter(tags=["subscriptions"])

_ORG_PUBLIC_ID = Path(description="External UUIDv7 of the organization (not internal BIGINT id).")
_SUB_PUBLIC_ID = Path(description="External UUIDv7 of the subscription (not internal BIGINT id).")


class SubscriptionResponse(BaseModel):
    """Public subscription representation (no internal BIGINT id)."""

    public_id: UUID = Field(
        description="External UUIDv7 of the subscription (not internal BIGINT id).",
    )
    organization_public_id: UUID = Field(
        description="External UUIDv7 of the organization (not internal BIGINT id).",
    )
    plan_id: UUID = Field(description="External UUIDv7 of the subscribed plan.")
    status: str = Field(
        description="Subscription lifecycle status (e.g. active, trialing, canceled).",
    )
    current_period_start: datetime = Field(
        description="Start of the current billing period (UTC).",
    )
    current_period_end: datetime = Field(description="End of the current billing period (UTC).")
    cancel_at_period_end: bool = Field(
        description="Whether cancellation is scheduled at period end.",
    )
    canceled_at: datetime | None = Field(
        default=None,
        description="UTC timestamp when the subscription was canceled, if applicable.",
    )
    trial_end: datetime | None = Field(
        default=None,
        description="UTC timestamp when the trial ends, if applicable.",
    )
    metadata: dict[str, object] = Field(
        default_factory=dict,
        description="Arbitrary JSON metadata stored with the subscription.",
    )
    created_at: datetime = Field(description="UTC timestamp when the subscription was created.")
    updated_at: datetime = Field(description="UTC timestamp of the last subscription update.")


class CreateSubscriptionRequest(BaseModel):
    organization_public_id: UUID = Field(
        description="External UUIDv7 of the organization (not internal BIGINT id).",
    )
    plan_id: UUID = Field(description="External UUIDv7 of the plan to subscribe to.")
    metadata: dict[str, object] = Field(
        default_factory=dict,
        description="Arbitrary JSON metadata stored with the subscription.",
    )


class CancelSubscriptionRequest(BaseModel):
    at_period_end: bool = Field(
        default=False,
        description="Cancel at period end instead of immediately.",
    )


class ChangePlanRequest(BaseModel):
    new_plan_id: UUID = Field(description="External UUIDv7 of the target plan.")
    effective: Literal["immediate"] = Field(
        default="immediate",
        description="When the plan change takes effect; only immediate is supported.",
    )


def _to_response(subscription: Subscription, organization: Organization) -> SubscriptionResponse:
    return SubscriptionResponse(
        public_id=subscription.public_id,
        organization_public_id=organization.public_id,
        plan_id=subscription.plan_id,
        status=subscription.status,
        current_period_start=subscription.current_period_start,
        current_period_end=subscription.current_period_end,
        cancel_at_period_end=subscription.cancel_at_period_end,
        canceled_at=subscription.canceled_at,
        trial_end=subscription.trial_end,
        metadata=subscription.metadata_,
        created_at=subscription.created_at,
        updated_at=subscription.updated_at,
    )


async def _load_organization_for_subscription(
    session: AsyncSession,
    subscription: Subscription,
) -> Organization:
    result = await session.execute(
        select(Organization).where(Organization.id == subscription.organization_id)
    )
    organization = result.scalar_one_or_none()
    if organization is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="organization not found")
    return organization


@router.post(
    "/subscriptions",
    response_model=SubscriptionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create subscription",
    description=(
        "Starts a subscription for an organization on a published plan. "
        "Tenant API keys for that organization or platform_admin. "
        "Idempotent via Idempotency-Key header. Plan must be published."
    ),
    responses=merge_responses(
        AUTH_RESPONSES,
        FORBIDDEN_RESPONSE,
        NOT_FOUND_RESPONSE,
        BAD_REQUEST_RESPONSE,
    ),
)
async def post_subscription(
    body: Annotated[
        CreateSubscriptionRequest,
        Body(openapi_examples=CREATE_SUBSCRIPTION_EXAMPLES),
    ],
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> SubscriptionResponse:
    organization = await get_organization_by_public_id(session, body.organization_public_id)
    if organization is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="organization not found")
    assert_tenant_access(ctx, organization)

    try:
        subscription = await create_subscription(
            session,
            organization_id=organization.id,
            plan_id=body.plan_id,
            idempotency_key=idempotency_key,
            metadata=body.metadata,
        )
    except PlanNotPublishedError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    await session.commit()
    return _to_response(subscription, organization)


@router.get(
    "/subscriptions/{subscription_public_id}",
    response_model=SubscriptionResponse,
    summary="Get subscription",
    description=(
        "Returns subscription status, billing period, trial, and cancellation "
        "details by public UUID. "
        "Tenant keys may only read subscriptions for their organization; "
        "platform_admin may read any."
    ),
    responses=merge_responses(AUTH_RESPONSES, FORBIDDEN_RESPONSE, NOT_FOUND_RESPONSE),
)
async def get_subscription(
    subscription_public_id: Annotated[UUID, _SUB_PUBLIC_ID],
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> SubscriptionResponse:
    subscription = await get_subscription_by_public_id(session, subscription_public_id)
    if subscription is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="subscription not found")

    organization = await _load_organization_for_subscription(session, subscription)
    assert_tenant_access(ctx, organization)
    return _to_response(subscription, organization)


@router.get(
    "/organizations/{organization_public_id}/subscriptions",
    response_model=list[SubscriptionResponse],
    summary="List organization subscriptions",
    description=(
        "Lists all subscriptions for an organization (active, trialing, canceled, etc.). "
        "Tenant keys may only list their own organization; platform_admin may list any."
    ),
    responses=merge_responses(AUTH_RESPONSES, FORBIDDEN_RESPONSE, NOT_FOUND_RESPONSE),
)
async def list_organization_subscriptions(
    organization_public_id: Annotated[UUID, _ORG_PUBLIC_ID],
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> list[SubscriptionResponse]:
    organization = await get_organization_by_public_id(session, organization_public_id)
    if organization is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="organization not found")
    assert_tenant_access(ctx, organization)

    subscriptions = await list_subscriptions_for_organization(
        session,
        organization_id=organization.id,
    )
    return [_to_response(sub, organization) for sub in subscriptions]


@router.post(
    "/subscriptions/{subscription_public_id}/cancel",
    response_model=SubscriptionResponse,
    summary="Cancel subscription",
    description=(
        "Cancels a subscription immediately or at the end of the current billing period. "
        "Tenant keys for that organization or platform_admin. "
        "Returns 409 if the subscription status transition is not allowed."
    ),
    responses=merge_responses(
        AUTH_RESPONSES,
        FORBIDDEN_RESPONSE,
        NOT_FOUND_RESPONSE,
        CONFLICT_RESPONSE,
    ),
)
async def post_cancel_subscription(
    subscription_public_id: Annotated[UUID, _SUB_PUBLIC_ID],
    body: CancelSubscriptionRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> SubscriptionResponse:
    subscription = await get_subscription_by_public_id(session, subscription_public_id)
    if subscription is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="subscription not found")

    organization = await _load_organization_for_subscription(session, subscription)
    if ctx.role != ApiKeyRole.PLATFORM_ADMIN.value:
        assert_tenant_access(ctx, organization)

    try:
        subscription = await cancel_subscription(
            session,
            subscription,
            at_period_end=body.at_period_end,
        )
    except IllegalTransition as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    await session.commit()
    return _to_response(subscription, organization)


@router.post(
    "/subscriptions/{subscription_public_id}/change-plan",
    response_model=SubscriptionResponse,
    summary="Change subscription plan",
    description=(
        "Switches a subscription to a different published plan with immediate effect. "
        "Tenant keys for that organization or platform_admin. "
        "Idempotent via Idempotency-Key header. Bumps entitlement cache on success."
    ),
    responses=merge_responses(
        AUTH_RESPONSES,
        FORBIDDEN_RESPONSE,
        NOT_FOUND_RESPONSE,
        BAD_REQUEST_RESPONSE,
        CONFLICT_RESPONSE,
    ),
)
async def post_change_plan(
    subscription_public_id: Annotated[UUID, _SUB_PUBLIC_ID],
    body: ChangePlanRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> SubscriptionResponse:
    subscription = await get_subscription_by_public_id(session, subscription_public_id)
    if subscription is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="subscription not found")

    organization = await _load_organization_for_subscription(session, subscription)
    assert_tenant_access(ctx, organization)

    try:
        subscription = await change_plan(
            session,
            subscription=subscription,
            new_plan_id=body.new_plan_id,
            effective=body.effective,
            idempotency_key=idempotency_key,
        )
    except PlanNotPublishedError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except PlanChangeNotAllowedError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    organization_id = subscription.organization_id
    await session.commit()
    await session.refresh(subscription)

    try:
        redis = await get_redis_client()
        await bump_entitlement_version(redis, organization_id=organization_id)
    except RedisError as exc:
        logger.warning(
            "entitlement_cache_bump_failed",
            organization_id=organization_id,
            error=str(exc),
        )

    return _to_response(subscription, organization)
