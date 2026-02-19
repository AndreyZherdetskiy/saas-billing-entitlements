from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from billing_platform.domain.models.api_key import ApiKey, ApiKeyRole
from billing_platform.domain.models.organization import Organization
from billing_platform.services.hotpath_cache import cache_auth_context, invalidate_auth_context


@dataclass(frozen=True, slots=True)
class AuthContext:
    organization_id: int | None
    role: str
    key_prefix: str
    api_key_id: UUID
    organization_public_id: UUID | None
    expires_at: datetime | None = None


def hash_api_key(raw: str) -> str:
    """Return SHA-256 hex digest of UTF-8 raw key (FIPS 180-4)."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def verify_api_key(raw: str, digest: str) -> bool:
    """Constant-time compare of hash_api_key(raw) vs stored digest."""
    return hmac.compare_digest(hash_api_key(raw), digest)


def _generate_raw_api_key() -> str:
    return f"bp_{secrets.token_urlsafe(32)}"


def _parse_role(role: str) -> str:
    try:
        return ApiKeyRole(role).value
    except ValueError:
        valid = ", ".join(sorted(r.value for r in ApiKeyRole))
        raise ValueError(f"invalid role '{role}'; expected one of: {valid}") from None


async def create_api_key(
    session: AsyncSession,
    *,
    organization_id: int | None,
    role: str,
    raw: str | None = None,
) -> tuple[ApiKey, str]:
    """Issue a new API key; raw secret is returned once and never stored.

    Pass ``raw`` only for local deterministic bootstrap (demo seed). Production
    issuance must omit it so secrets stay random.
    """
    parsed_role = _parse_role(role)
    if parsed_role != ApiKeyRole.PLATFORM_ADMIN.value and organization_id is None:
        raise ValueError("organization_id is required for non-platform_admin keys")

    raw_key = raw if raw is not None else _generate_raw_api_key()
    if not raw_key.startswith("bp_"):
        raise ValueError("API key must start with 'bp_'")
    key_prefix = raw_key[:8]
    api_key = ApiKey(
        organization_id=organization_id,
        key_hash=hash_api_key(raw_key),
        key_prefix=key_prefix,
        role=parsed_role,
    )
    session.add(api_key)
    await session.flush()
    return api_key, raw_key


async def _load_active_api_key(session: AsyncSession, key_id: UUID) -> ApiKey:
    result = await session.execute(
        select(ApiKey).where(
            ApiKey.id == key_id,
            ApiKey.revoked_at.is_(None),
        )
    )
    api_key = result.scalar_one_or_none()
    if api_key is None:
        raise ValueError("api key not found")
    return api_key


def _assert_key_belongs_to_organization(api_key: ApiKey, organization_id: int) -> None:
    if api_key.organization_id is not None and api_key.organization_id != organization_id:
        raise ValueError("api key does not belong to organization")


async def rotate_api_key(
    session: AsyncSession,
    *,
    organization_id: int,
    actor_key_id: UUID,
) -> tuple[ApiKey, str]:
    """Issue a replacement key; the old key stays valid until revoked."""
    api_key = await _load_active_api_key(session, actor_key_id)
    _assert_key_belongs_to_organization(api_key, organization_id)
    new_key, raw_key = await create_api_key(
        session,
        organization_id=api_key.organization_id,
        role=api_key.role,
    )
    invalidate_auth_context(api_key_id=actor_key_id)
    return new_key, raw_key


async def revoke_api_key(
    session: AsyncSession,
    *,
    organization_id: int,
    actor_key_id: UUID,
) -> ApiKey:
    """Revoke an API key after the overlap window."""
    api_key = await _load_active_api_key(session, actor_key_id)
    _assert_key_belongs_to_organization(api_key, organization_id)
    api_key.revoked_at = datetime.now(UTC)
    await session.flush()
    invalidate_auth_context(api_key_id=actor_key_id)
    return api_key


async def authenticate(session: AsyncSession, bearer: str) -> AuthContext:
    """Lookup by unique key_hash; no candidate loop."""
    if not bearer:
        raise ValueError("missing bearer token")

    lookup = hash_api_key(bearer)
    result = await session.execute(
        select(ApiKey, Organization)
        .outerjoin(Organization, ApiKey.organization_id == Organization.id)
        .where(
            ApiKey.key_hash == lookup,
            ApiKey.revoked_at.is_(None),
        )
    )
    row = result.one_or_none()
    if row is None:
        raise ValueError("invalid or expired API key")
    api_key, organization = row
    if api_key.expires_at is not None and api_key.expires_at <= datetime.now(UTC):
        raise ValueError("invalid or expired API key")
    ctx = AuthContext(
        organization_id=api_key.organization_id,
        role=api_key.role,
        key_prefix=api_key.key_prefix,
        api_key_id=api_key.id,
        organization_public_id=None if organization is None else organization.public_id,
        expires_at=api_key.expires_at,
    )
    cache_auth_context(lookup, ctx)
    return ctx


async def resolve_organization_by_public_id(
    session: AsyncSession,
    public_id: object,
) -> Organization | None:
    if not isinstance(public_id, UUID):
        public_id = UUID(str(public_id))
    result = await session.execute(
        select(Organization).where(
            Organization.public_id == public_id,
            Organization.deleted_at.is_(None),
        )
    )
    return result.scalar_one_or_none()
