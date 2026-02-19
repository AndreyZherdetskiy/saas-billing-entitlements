"""API key rotation admin routes."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from billing_platform.api.deps import assert_tenant_access, get_auth_context
from billing_platform.api.openapi_docs import (
    AUTH_RESPONSES,
    BAD_REQUEST_RESPONSE,
    FORBIDDEN_RESPONSE,
    NOT_FOUND_RESPONSE,
    merge_responses,
)
from billing_platform.db import get_session
from billing_platform.domain.models.api_key import ApiKey, ApiKeyRole
from billing_platform.domain.models.organization import Organization
from billing_platform.logging import get_logger
from billing_platform.services.api_keys import (
    AuthContext,
    revoke_api_key,
    rotate_api_key,
)

router = APIRouter(prefix="/admin/api-keys", tags=["api-keys"])
logger = get_logger(__name__)

_KEY_ID = Path(description="External UUIDv7 of the API key (not internal BIGINT id).")

_READ_ONLY_ROLES = frozenset(
    {
        ApiKeyRole.REVOPS_READ.value,
        ApiKeyRole.SUPPORT_READ.value,
    }
)


class ApiKeyRotatedResponse(BaseModel):
    """New API key returned once after rotation."""

    id: UUID = Field(description="External UUIDv7 of the new API key (not internal BIGINT id).")
    key_prefix: str = Field(description="Short prefix shown in logs and admin UIs.")
    role: str = Field(description="Role granted to the new API key.")
    raw_key: str = Field(
        description="Full secret key returned once; store securely — cannot be retrieved again.",
    )


class ApiKeyRevokedResponse(BaseModel):
    """Revoked API key metadata (no secret)."""

    id: UUID = Field(
        description="External UUIDv7 of the revoked API key (not internal BIGINT id).",
    )
    key_prefix: str = Field(description="Short prefix shown in logs and admin UIs.")
    role: str = Field(description="Role that was granted to the revoked key.")
    revoked_at: datetime = Field(description="UTC timestamp when the key was revoked.")


async def _load_organization(
    session: AsyncSession,
    organization_id: int,
) -> Organization | None:
    result = await session.execute(
        select(Organization).where(
            Organization.id == organization_id,
            Organization.deleted_at.is_(None),
        )
    )
    return result.scalar_one_or_none()


async def _load_api_key(session: AsyncSession, key_id: UUID) -> ApiKey | None:
    result = await session.execute(select(ApiKey).where(ApiKey.id == key_id))
    return result.scalar_one_or_none()


def _assert_rotate_access(ctx: AuthContext, api_key: ApiKey) -> None:
    if ctx.role == ApiKeyRole.PLATFORM_ADMIN.value:
        return
    if ctx.api_key_id != api_key.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="only platform_admin or the key owner may rotate this api key",
        )
    if api_key.organization_id is None or ctx.organization_id != api_key.organization_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="cross-tenant access denied",
        )


def _assert_revoke_access(ctx: AuthContext, api_key: ApiKey) -> None:
    if ctx.role == ApiKeyRole.PLATFORM_ADMIN.value:
        return
    if api_key.organization_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="only platform_admin may revoke platform_admin keys",
        )
    if ctx.organization_id != api_key.organization_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="cross-tenant access denied",
        )
    if ctx.api_key_id == api_key.id:
        return
    if ctx.role in _READ_ONLY_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="read-only roles may not revoke other api keys",
        )
    if ctx.role == api_key.role:
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="only platform_admin, self-revoke, or same-role overlap revoke allowed",
    )


async def _resolve_organization_id(
    session: AsyncSession,
    ctx: AuthContext,
    api_key: ApiKey,
) -> int:
    if api_key.organization_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="platform_admin keys cannot be rotated via this endpoint",
        )
    organization = await _load_organization(session, api_key.organization_id)
    if organization is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="organization not found",
        )
    assert_tenant_access(ctx, organization)
    return organization.id


@router.post(
    "/{key_id}/rotate",
    response_model=ApiKeyRotatedResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Rotate API key",
    description=(
        "Creates a replacement API key for the same role; "
        "the old key remains valid until revoked. "
        "Platform_admin may rotate any tenant key; a tenant key may rotate only itself. "
        "Returns the raw new key once — store it securely."
    ),
    responses=merge_responses(
        AUTH_RESPONSES,
        FORBIDDEN_RESPONSE,
        NOT_FOUND_RESPONSE,
        BAD_REQUEST_RESPONSE,
    ),
)
async def post_rotate_api_key(
    key_id: Annotated[UUID, _KEY_ID],
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> ApiKeyRotatedResponse:
    """Create a replacement key; old and new are both valid until revoke."""
    api_key = await _load_api_key(session, key_id)
    if api_key is None or api_key.revoked_at is not None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="api key not found",
        )
    _assert_rotate_access(ctx, api_key)
    organization_id = await _resolve_organization_id(session, ctx, api_key)
    try:
        new_key, raw = await rotate_api_key(
            session,
            organization_id=organization_id,
            actor_key_id=key_id,
        )
    except ValueError as exc:
        detail = str(exc)
        code = status.HTTP_404_NOT_FOUND if "not found" in detail else status.HTTP_400_BAD_REQUEST
        raise HTTPException(status_code=code, detail=detail) from exc

    await session.commit()
    logger.info(
        "api_key_rotated",
        old_key_id=str(key_id),
        new_key_id=str(new_key.id),
        organization_id=organization_id,
    )
    return ApiKeyRotatedResponse(
        id=new_key.id,
        key_prefix=new_key.key_prefix,
        role=new_key.role,
        raw_key=raw,
    )


@router.post(
    "/{key_id}/revoke",
    response_model=ApiKeyRevokedResponse,
    summary="Revoke API key",
    description=(
        "Permanently revokes an API key after clients have switched to a replacement. "
        "Platform_admin may revoke any tenant key; tenant keys may self-revoke or revoke keys "
        "with the same role. Read-only roles cannot revoke other keys."
    ),
    responses=merge_responses(
        AUTH_RESPONSES,
        FORBIDDEN_RESPONSE,
        NOT_FOUND_RESPONSE,
        BAD_REQUEST_RESPONSE,
    ),
)
async def post_revoke_api_key(
    key_id: Annotated[UUID, _KEY_ID],
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> ApiKeyRevokedResponse:
    """Revoke an API key after clients have switched to the replacement."""
    api_key = await _load_api_key(session, key_id)
    if api_key is None or api_key.revoked_at is not None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="api key not found",
        )
    _assert_revoke_access(ctx, api_key)
    organization_id = await _resolve_organization_id(session, ctx, api_key)
    try:
        revoked = await revoke_api_key(
            session,
            organization_id=organization_id,
            actor_key_id=key_id,
        )
    except ValueError as exc:
        detail = str(exc)
        code = status.HTTP_404_NOT_FOUND if "not found" in detail else status.HTTP_400_BAD_REQUEST
        raise HTTPException(status_code=code, detail=detail) from exc

    await session.commit()
    assert revoked.revoked_at is not None
    logger.info(
        "api_key_revoked",
        key_id=str(key_id),
        organization_id=organization_id,
    )
    return ApiKeyRevokedResponse(
        id=revoked.id,
        key_prefix=revoked.key_prefix,
        role=revoked.role,
        revoked_at=revoked.revoked_at,
    )
