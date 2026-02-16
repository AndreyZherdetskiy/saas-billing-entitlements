"""FastAPI dependencies."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from billing_platform.config import get_settings
from billing_platform.db import get_read_session
from billing_platform.domain.models.api_key import ApiKeyRole
from billing_platform.domain.models.organization import Organization
from billing_platform.middleware.rate_limit import (
    enforce_rate_limit_for_api_key,
    is_rate_limit_exempt,
)
from billing_platform.middleware.request_context import bind_organization_id
from billing_platform.services.api_keys import AuthContext, authenticate, hash_api_key
from billing_platform.services.hotpath_cache import get_cached_auth_context
from billing_platform.services.rate_limit import resolve_api_rate_limit_per_minute

# Registers Bearer in OpenAPI so Swagger Authorize injects Authorization.
_bearer_scheme = HTTPBearer(auto_error=False)


async def acquire_read_session(request: Request) -> AsyncIterator[AsyncSession]:
    """Open a read session only on cache miss; honor FastAPI dependency_overrides."""
    getter = request.app.dependency_overrides.get(get_read_session, get_read_session)
    async for session in getter():
        yield session


def _bind_request_org(request: Request, ctx: AuthContext) -> None:
    if ctx.organization_id is None or ctx.organization_public_id is None:
        return
    public_id = str(ctx.organization_public_id)
    bind_organization_id(public_id)
    request.state.organization_id = public_id


async def get_auth_context(
    request: Request,
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(_bearer_scheme),
    ] = None,
) -> AuthContext:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing or invalid authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )
    bearer = credentials.credentials.strip()
    digest = hash_api_key(bearer)
    ctx = get_cached_auth_context(digest)
    if ctx is None:
        try:
            async for session in acquire_read_session(request):
                ctx = await authenticate(session, bearer)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=str(exc),
                headers={"WWW-Authenticate": "Bearer"},
            ) from exc
    if ctx is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing or invalid authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )

    request.state.api_key_id = ctx.api_key_id
    if not is_rate_limit_exempt(request.url.path):
        settings = get_settings()
        limit = resolve_api_rate_limit_per_minute(role=ctx.role, settings=settings)
        await enforce_rate_limit_for_api_key(
            api_key_id=ctx.api_key_id,
            limit_per_minute=limit,
        )

    _bind_request_org(request, ctx)
    return ctx


async def require_platform_admin(
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> AuthContext:
    """Allow only platform_admin for catalog admin routes."""
    if ctx.role != ApiKeyRole.PLATFORM_ADMIN.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="only platform_admin may access catalog admin routes",
        )
    return ctx


async def require_dunning_operator(
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> AuthContext:
    """Allow platform_admin or dunning_operator for dunning admin routes."""
    if ctx.role not in (
        ApiKeyRole.PLATFORM_ADMIN.value,
        ApiKeyRole.DUNNING_OPERATOR.value,
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="only platform_admin or dunning_operator may manage dunning campaigns",
        )
    return ctx


def assert_tenant_access(ctx: AuthContext, organization: Organization) -> None:
    """Raise 403 when the auth context cannot access the organization."""
    assert_tenant_access_org_id(ctx, organization.id)


def assert_tenant_access_org_id(ctx: AuthContext, organization_id: int) -> None:
    """Raise 403 when the auth context cannot access organization_id."""
    if ctx.role == ApiKeyRole.PLATFORM_ADMIN.value:
        return
    if ctx.organization_id is None or ctx.organization_id != organization_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="cross-tenant access denied",
        )


def assert_ledger_read_access(ctx: AuthContext, organization: Organization) -> None:
    """Allow platform_admin, revops_read, or tenant-scoped keys for ledger reads."""
    if ctx.role in (ApiKeyRole.PLATFORM_ADMIN.value, ApiKeyRole.REVOPS_READ.value):
        return
    assert_tenant_access(ctx, organization)
