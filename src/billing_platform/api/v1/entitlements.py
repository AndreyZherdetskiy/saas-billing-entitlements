"""Entitlement evaluate and admin invalidate routes (API D)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from billing_platform.api.deps import (
    acquire_read_session,
    assert_tenant_access,
    assert_tenant_access_org_id,
    get_auth_context,
    require_platform_admin,
)
from billing_platform.api.openapi_docs import (
    AUTH_RESPONSES,
    BAD_REQUEST_RESPONSE,
    EVALUATE_ENTITLEMENTS_EXAMPLES,
    FORBIDDEN_RESPONSE,
    NOT_FOUND_RESPONSE,
    merge_responses,
)
from billing_platform.db import get_session
from billing_platform.domain.models.api_key import ApiKeyRole
from billing_platform.integrations.redis_cache import get_redis_client
from billing_platform.services.api_keys import AuthContext
from billing_platform.services.entitlements import (
    Check,
    EntitlementError,
    OrganizationNotFoundError,
    SubscriptionNotFoundError,
    bump_entitlement_version,
    evaluate,
)
from billing_platform.services.hotpath_cache import get_l1_org, get_l1_snapshot, set_l1_org
from billing_platform.services.organizations import get_organization_by_public_id

router = APIRouter(prefix="/entitlements", tags=["entitlements"])


class CheckRequest(BaseModel):
    feature_key: str = Field(description="Feature key defined on the plan entitlements.")
    quantity: int = Field(default=1, description="Requested quantity for quota/rate/seat checks.")


class EvaluateRequest(BaseModel):
    organization_public_id: UUID = Field(
        description="External UUIDv7 of the organization (not the internal BIGINT id).",
    )
    checks: list[CheckRequest] = Field(
        min_length=1,
        description="One or more feature checks to evaluate.",
    )


class EvaluateResultResponse(BaseModel):
    feature_key: str = Field(description="Feature key that was evaluated.")
    feature_type: str = Field(
        description="Entitlement feature type: boolean, quota, rate_limit, or seat.",
        examples=["quota"],
    )
    allowed: bool = Field(description="Whether the requested quantity is allowed.")
    limit: int | None = Field(
        default=None,
        description="Configured limit for quota/seat features.",
    )
    used: int | None = Field(default=None, description="Current usage against the limit.")
    remaining: int | None = Field(default=None, description="Remaining capacity before denial.")
    reason: str | None = Field(
        default=None,
        description="Denial reason when allowed is false.",
        examples=["quota_exhausted", "rate_limit_exhausted", "seat_exhausted"],
    )


class EvaluateResponseModel(BaseModel):
    organization_public_id: str = Field(
        description="External UUIDv7 of the organization that was evaluated.",
    )
    subscription_status: str = Field(description="Subscription status used for evaluation.")
    results: list[EvaluateResultResponse] = Field(
        description="Per-feature evaluation outcomes in request order.",
    )
    cache_hit: bool = Field(
        description="Whether the result came from the process-local or Redis entitlement cache.",
    )
    evaluated_at: datetime = Field(description="UTC timestamp when evaluation completed.")
    version: int = Field(description="Entitlement cache version after evaluation.")


class InvalidateRequest(BaseModel):
    organization_public_id: UUID = Field(
        description="External UUIDv7 of the organization whose entitlement cache to bump.",
    )


class InvalidateResponse(BaseModel):
    organization_public_id: str = Field(
        description="External UUIDv7 of the organization whose cache was invalidated.",
    )
    version: int = Field(description="New entitlement cache version after the bump.")


async def get_redis() -> Redis:
    """FastAPI dependency for the shared Redis client."""
    return await get_redis_client()


async def _acquire_redis(request: Request) -> Redis:
    getter = request.app.dependency_overrides.get(get_redis, get_redis)
    client = await getter()
    if not isinstance(client, Redis):
        msg = "redis dependency did not return a Redis client"
        raise TypeError(msg)
    return client


def _entitlement_error_to_http(exc: EntitlementError) -> HTTPException:
    if isinstance(exc, OrganizationNotFoundError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, SubscriptionNotFoundError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.post(
    "/evaluate",
    response_model=EvaluateResponseModel,
    summary="Evaluate entitlements",
    description=(
        "Read-only entitlement checks for one or more features on an organization. "
        "Uses Redis-cached snapshots and does not write usage. "
        "Tenant keys may only evaluate their own organization; platform_admin may evaluate any."
    ),
    responses=merge_responses(
        AUTH_RESPONSES,
        FORBIDDEN_RESPONSE,
        NOT_FOUND_RESPONSE,
        BAD_REQUEST_RESPONSE,
    ),
)
async def post_evaluate(
    request: Request,
    body: Annotated[
        EvaluateRequest,
        Body(openapi_examples=EVALUATE_ENTITLEMENTS_EXAMPLES),
    ],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> EvaluateResponseModel:
    """Read-only entitlement evaluation; no session or Redis GET on full L1 hit."""
    if (
        ctx.role != ApiKeyRole.PLATFORM_ADMIN.value
        and ctx.organization_public_id is not None
        and ctx.organization_public_id != body.organization_public_id
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="cross-tenant access denied",
        )

    org_id: int | None = None
    public_id: UUID | None = None
    if (
        ctx.organization_id is not None
        and ctx.organization_public_id == body.organization_public_id
    ):
        org_id = ctx.organization_id
        public_id = ctx.organization_public_id
    else:
        cached = get_l1_org(body.organization_public_id)
        if cached is not None:
            org_id, public_id = cached
            assert_tenant_access_org_id(ctx, org_id)

    checks = [Check(feature_key=item.feature_key, quantity=item.quantity) for item in body.checks]

    try:
        if org_id is not None and public_id is not None and get_l1_snapshot(org_id) is not None:
            response = await evaluate(
                None,
                organization_id=org_id,
                organization_public_id=public_id,
                checks=checks,
                session=None,
            )
        elif org_id is not None and public_id is not None:

            async def session_provider() -> AsyncIterator[AsyncSession]:
                async for session in acquire_read_session(request):
                    yield session

            response = await evaluate(
                await _acquire_redis(request),
                organization_id=org_id,
                organization_public_id=public_id,
                checks=checks,
                session=None,
                session_provider=session_provider,
            )
        else:
            redis = await _acquire_redis(request)
            async for session in acquire_read_session(request):
                organization = await get_organization_by_public_id(
                    session, body.organization_public_id
                )
                if organization is None:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail=f"organization {body.organization_public_id} not found",
                    )
                assert_tenant_access(ctx, organization)
                org_id = organization.id
                public_id = organization.public_id
                set_l1_org(public_id, org_id)
                response = await evaluate(
                    redis,
                    organization_id=org_id,
                    organization_public_id=public_id,
                    checks=checks,
                    session=session,
                )
                break
            else:
                msg = "database session unavailable"
                raise RuntimeError(msg)
    except EntitlementError as exc:
        raise _entitlement_error_to_http(exc) from exc

    return EvaluateResponseModel(
        organization_public_id=response.organization_public_id,
        subscription_status=response.subscription_status,
        results=[
            EvaluateResultResponse(
                feature_key=result.feature_key,
                feature_type=result.feature_type,
                allowed=result.allowed,
                limit=result.limit,
                used=result.used,
                remaining=result.remaining,
                reason=result.reason,
            )
            for result in response.results
        ],
        cache_hit=response.cache_hit,
        evaluated_at=response.evaluated_at,
        version=response.version,
    )


@router.post(
    "/invalidate",
    response_model=InvalidateResponse,
    summary="Invalidate entitlement cache",
    description=(
        "Bumps the Redis entitlement cache version for an organization so the next evaluate "
        "rebuilds from the database. Platform_admin only."
    ),
    responses=merge_responses(AUTH_RESPONSES, FORBIDDEN_RESPONSE, NOT_FOUND_RESPONSE),
)
async def post_invalidate(
    body: InvalidateRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    redis: Annotated[Redis, Depends(get_redis)],
    _: Annotated[AuthContext, Depends(require_platform_admin)],
) -> InvalidateResponse:
    """Admin: bump entitlement cache version for an organization."""
    organization = await get_organization_by_public_id(session, body.organization_public_id)
    if organization is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"organization {body.organization_public_id} not found",
        )

    new_version = await bump_entitlement_version(redis, organization_id=organization.id)
    return InvalidateResponse(
        organization_public_id=str(organization.public_id),
        version=new_version,
    )
