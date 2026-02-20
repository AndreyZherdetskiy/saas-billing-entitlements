"""Organization HTTP routes (API A)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Path, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from billing_platform.api.deps import assert_tenant_access, get_auth_context
from billing_platform.api.openapi_docs import (
    AUTH_RESPONSES,
    BAD_REQUEST_RESPONSE,
    CREATE_ORGANIZATION_EXAMPLES,
    FORBIDDEN_RESPONSE,
    NOT_FOUND_RESPONSE,
    merge_responses,
)
from billing_platform.db import get_session
from billing_platform.domain.models.api_key import ApiKeyRole
from billing_platform.domain.models.organization import Organization
from billing_platform.services.api_keys import AuthContext, create_api_key
from billing_platform.services.organizations import (
    create_organization,
    get_organization_by_public_id,
    update_organization_metadata,
)

router = APIRouter(prefix="/organizations", tags=["organizations"])

_ORG_PUBLIC_ID = Path(description="External UUIDv7 of the organization (not internal BIGINT id).")


class OrganizationResponse(BaseModel):
    """Public organization representation (no internal BIGINT id)."""

    public_id: UUID = Field(
        description="External UUIDv7 of the organization (not internal BIGINT id).",
    )
    external_id: str | None = Field(
        default=None,
        description="Caller-defined stable external identifier for the organization.",
    )
    name: str = Field(description="Display name of the organization.")
    billing_email: str | None = Field(
        default=None,
        description="Primary billing contact email.",
    )
    metadata: dict[str, object] = Field(
        default_factory=dict,
        description="Arbitrary JSON metadata stored with the organization.",
    )
    created_at: datetime = Field(description="UTC timestamp when the organization was created.")
    updated_at: datetime = Field(description="UTC timestamp of the last organization update.")


class CreateOrganizationRequest(BaseModel):
    name: str = Field(description="Display name of the organization.")
    external_id: str = Field(
        description="Caller-defined stable external identifier; unique across organizations.",
    )
    billing_email: str | None = Field(
        default=None,
        description="Primary billing contact email.",
    )
    metadata: dict[str, object] = Field(
        default_factory=dict,
        description="Arbitrary JSON metadata stored with the organization.",
    )


class PatchOrganizationRequest(BaseModel):
    name: str | None = Field(default=None, description="Updated display name.")
    billing_email: str | None = Field(
        default=None,
        description="Updated billing contact email.",
    )
    metadata: dict[str, object] | None = Field(
        default=None,
        description="Replacement metadata object; omitted fields are not changed.",
    )


class CreateApiKeyRequest(BaseModel):
    role: str = Field(
        description="API key role (e.g. tenant_admin, product_service, platform_admin).",
    )


class ApiKeyCreatedResponse(BaseModel):
    id: UUID = Field(description="External UUIDv7 of the API key (not internal BIGINT id).")
    key_prefix: str = Field(description="Short prefix shown in logs and admin UIs.")
    role: str = Field(description="Role granted to the new API key.")
    raw_key: str = Field(
        description="Full secret key returned once; store securely — cannot be retrieved again.",
    )


def _to_response(org: Organization) -> OrganizationResponse:
    return OrganizationResponse(
        public_id=org.public_id,
        external_id=org.external_id,
        name=org.name,
        billing_email=org.billing_email,
        metadata=org.metadata_,
        created_at=org.created_at,
        updated_at=org.updated_at,
    )


@router.post(
    "",
    response_model=OrganizationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create organization",
    description=(
        "Registers a new tenant organization with external_id, name, billing email, and metadata. "
        "Platform_admin only. Idempotent via Idempotency-Key header — repeating the same key "
        "returns the original organization."
    ),
    responses=merge_responses(AUTH_RESPONSES, FORBIDDEN_RESPONSE),
)
async def post_organization(
    body: Annotated[
        CreateOrganizationRequest,
        Body(openapi_examples=CREATE_ORGANIZATION_EXAMPLES),
    ],
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> OrganizationResponse:
    if ctx.role != ApiKeyRole.PLATFORM_ADMIN.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="only platform_admin may create organizations",
        )
    organization = await create_organization(
        session,
        name=body.name,
        external_id=body.external_id,
        idempotency_key=idempotency_key,
        billing_email=body.billing_email,
        metadata=body.metadata,
    )
    await session.commit()
    return _to_response(organization)


@router.get(
    "/{organization_public_id}",
    response_model=OrganizationResponse,
    summary="Get organization",
    description=(
        "Returns the organization profile (name, billing email, metadata) by its public UUID. "
        "Tenant API keys may only read their own organization; platform_admin may read any."
    ),
    responses=merge_responses(AUTH_RESPONSES, FORBIDDEN_RESPONSE, NOT_FOUND_RESPONSE),
)
async def get_organization(
    organization_public_id: Annotated[UUID, _ORG_PUBLIC_ID],
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> OrganizationResponse:
    organization = await get_organization_by_public_id(session, organization_public_id)
    if organization is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="organization not found")
    assert_tenant_access(ctx, organization)
    return _to_response(organization)


@router.patch(
    "/{organization_public_id}",
    response_model=OrganizationResponse,
    summary="Update organization",
    description=(
        "Updates mutable organization fields (name, billing email, metadata). "
        "Platform_admin only."
    ),
    responses=merge_responses(AUTH_RESPONSES, FORBIDDEN_RESPONSE, NOT_FOUND_RESPONSE),
)
async def patch_organization(
    organization_public_id: Annotated[UUID, _ORG_PUBLIC_ID],
    body: PatchOrganizationRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> OrganizationResponse:
    organization = await get_organization_by_public_id(session, organization_public_id)
    if organization is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="organization not found")
    if ctx.role != ApiKeyRole.PLATFORM_ADMIN.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="only platform_admin may update organizations",
        )
    organization = await update_organization_metadata(
        session,
        organization,
        name=body.name,
        billing_email=body.billing_email,
        metadata=body.metadata,
    )
    await session.commit()
    return _to_response(organization)


@router.post(
    "/{organization_public_id}/api-keys",
    response_model=ApiKeyCreatedResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Issue API key",
    description=(
        "Creates a new API key for the organization, or a platform-wide platform_admin key "
        "when that role is requested. Platform_admin only. Returns the raw key once in the "
        "response — store it securely; it cannot be retrieved again."
    ),
    responses=merge_responses(
        AUTH_RESPONSES,
        FORBIDDEN_RESPONSE,
        NOT_FOUND_RESPONSE,
        BAD_REQUEST_RESPONSE,
    ),
)
async def post_api_key(
    organization_public_id: Annotated[UUID, _ORG_PUBLIC_ID],
    body: CreateApiKeyRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> ApiKeyCreatedResponse:
    if ctx.role != ApiKeyRole.PLATFORM_ADMIN.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="only platform_admin may issue api keys",
        )
    organization = await get_organization_by_public_id(session, organization_public_id)
    if organization is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="organization not found")

    org_id: int | None = organization.id
    if body.role == ApiKeyRole.PLATFORM_ADMIN.value:
        org_id = None

    try:
        api_key, raw = await create_api_key(
            session,
            organization_id=org_id,
            role=body.role,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    await session.commit()
    return ApiKeyCreatedResponse(
        id=api_key.id,
        key_prefix=api_key.key_prefix,
        role=api_key.role,
        raw_key=raw,
    )
