"""Unit tests for API key issuance and authentication."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from billing_platform.domain.models.api_key import ApiKey, ApiKeyRole
from billing_platform.services.api_keys import (
    authenticate,
    create_api_key,
    hash_api_key,
    resolve_organization_by_public_id,
    revoke_api_key,
    rotate_api_key,
)
from billing_platform.services.hotpath_cache import get_cached_auth_context
from billing_platform.services.organizations import create_organization


@pytest.mark.asyncio
async def test_create_platform_admin_key(db_session: AsyncSession) -> None:
    api_key, raw = await create_api_key(
        db_session,
        organization_id=None,
        role=ApiKeyRole.PLATFORM_ADMIN.value,
    )
    assert api_key.role == ApiKeyRole.PLATFORM_ADMIN.value
    assert raw.startswith("bp_")
    assert api_key.key_prefix == raw[:8]


@pytest.mark.asyncio
async def test_create_org_key_requires_organization_id(db_session: AsyncSession) -> None:
    with pytest.raises(ValueError, match="organization_id is required"):
        await create_api_key(
            db_session,
            organization_id=None,
            role=ApiKeyRole.PRODUCT_SERVICE.value,
        )


@pytest.mark.asyncio
async def test_create_api_key_invalid_role(db_session: AsyncSession) -> None:
    with pytest.raises(ValueError, match="invalid role"):
        await create_api_key(
            db_session,
            organization_id=1,
            role="not_a_role",
        )


@pytest.mark.asyncio
async def test_authenticate_valid_key(db_session: AsyncSession) -> None:
    org = await create_organization(
        db_session,
        name="Auth Org",
        external_id=f"ext-auth-{uuid.uuid4().hex[:8]}",
        idempotency_key=f"idem-auth-{uuid.uuid4().hex[:8]}",
    )
    api_key, raw = await create_api_key(
        db_session,
        organization_id=org.id,
        role=ApiKeyRole.PRODUCT_SERVICE.value,
    )
    await db_session.commit()

    ctx = await authenticate(db_session, raw)
    assert ctx.organization_id == org.id
    assert ctx.role == ApiKeyRole.PRODUCT_SERVICE.value
    assert ctx.key_prefix == api_key.key_prefix
    assert ctx.api_key_id == api_key.id
    assert ctx.organization_public_id == org.public_id
    assert len(api_key.key_hash) == 64
    assert not api_key.key_hash.startswith("$2")
    assert api_key.key_hash == hash_api_key(raw)

    matching = await db_session.execute(select(ApiKey).where(ApiKey.key_hash == hash_api_key(raw)))
    assert len(matching.scalars().all()) == 1


@pytest.mark.asyncio
async def test_authenticate_missing_bearer(db_session: AsyncSession) -> None:
    with pytest.raises(ValueError, match="missing bearer token"):
        await authenticate(db_session, "")


@pytest.mark.asyncio
async def test_authenticate_invalid_key(db_session: AsyncSession) -> None:
    with pytest.raises(ValueError, match="invalid or expired"):
        await authenticate(db_session, "bp_invalid_key_value_001")


@pytest.mark.asyncio
async def test_authenticate_expired_key_skipped(db_session: AsyncSession) -> None:
    org = await create_organization(
        db_session,
        name="Expired Org",
        external_id=f"ext-exp-{uuid.uuid4().hex[:8]}",
        idempotency_key=f"idem-exp-{uuid.uuid4().hex[:8]}",
    )
    api_key, raw = await create_api_key(
        db_session,
        organization_id=org.id,
        role=ApiKeyRole.PRODUCT_SERVICE.value,
    )
    api_key.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    await db_session.commit()

    with pytest.raises(ValueError, match="invalid or expired"):
        await authenticate(db_session, raw)


@pytest.mark.asyncio
async def test_resolve_organization_by_public_id(db_session: AsyncSession) -> None:
    org = await create_organization(
        db_session,
        name="Resolve Org",
        external_id=f"ext-res-{uuid.uuid4().hex[:8]}",
        idempotency_key=f"idem-res-{uuid.uuid4().hex[:8]}",
    )
    await db_session.commit()

    by_uuid = await resolve_organization_by_public_id(db_session, org.public_id)
    by_str = await resolve_organization_by_public_id(db_session, str(org.public_id))
    assert by_uuid is not None
    assert by_str is not None
    assert by_uuid.id == org.id


@pytest.mark.asyncio
async def test_rotate_api_key_overlap_then_revoke(db_session: AsyncSession) -> None:
    org = await create_organization(
        db_session,
        name="Rotate Unit Org",
        external_id=f"ext-ru-{uuid.uuid4().hex[:8]}",
        idempotency_key=f"idem-ru-{uuid.uuid4().hex[:8]}",
    )
    old_key, old_raw = await create_api_key(
        db_session,
        organization_id=org.id,
        role=ApiKeyRole.PRODUCT_SERVICE.value,
    )
    await db_session.commit()

    new_key, new_raw = await rotate_api_key(
        db_session,
        organization_id=org.id,
        actor_key_id=old_key.id,
    )
    await db_session.commit()

    await authenticate(db_session, old_raw)
    await authenticate(db_session, new_raw)

    await revoke_api_key(
        db_session,
        organization_id=org.id,
        actor_key_id=old_key.id,
    )
    await db_session.commit()

    with pytest.raises(ValueError, match="invalid or expired"):
        await authenticate(db_session, old_raw)
    await authenticate(db_session, new_raw)
    assert new_key.id != old_key.id


@pytest.mark.asyncio
async def test_rotate_api_key_wrong_organization(db_session: AsyncSession) -> None:
    org = await create_organization(
        db_session,
        name="Wrong Org",
        external_id=f"ext-wo-{uuid.uuid4().hex[:8]}",
        idempotency_key=f"idem-wo-{uuid.uuid4().hex[:8]}",
    )
    old_key, _raw = await create_api_key(
        db_session,
        organization_id=org.id,
        role=ApiKeyRole.PRODUCT_SERVICE.value,
    )
    await db_session.commit()

    with pytest.raises(ValueError, match="does not belong"):
        await rotate_api_key(
            db_session,
            organization_id=org.id + 999,
            actor_key_id=old_key.id,
        )


@pytest.mark.asyncio
async def test_api_key_hash_is_unique(db_session: AsyncSession) -> None:
    org = await create_organization(
        db_session,
        name="Unique Hash Org",
        external_id=f"ext-uq-{uuid.uuid4().hex[:8]}",
        idempotency_key=f"idem-uq-{uuid.uuid4().hex[:8]}",
    )
    api_key, raw = await create_api_key(
        db_session,
        organization_id=org.id,
        role=ApiKeyRole.PRODUCT_SERVICE.value,
    )
    await db_session.commit()

    assert not api_key.key_hash.startswith("$2")
    assert api_key.key_hash == hash_api_key(raw)

    db_session.add(
        ApiKey(
            organization_id=org.id,
            key_hash=api_key.key_hash,
            key_prefix="bp_dup01",
            role=ApiKeyRole.PRODUCT_SERVICE.value,
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_authenticate_caches_and_revoke_invalidates(db_session: AsyncSession) -> None:
    org = await create_organization(
        db_session,
        name="Auth Cache Org",
        external_id=f"ext-ac-{uuid.uuid4().hex[:8]}",
        idempotency_key=f"idem-ac-{uuid.uuid4().hex[:8]}",
    )
    api_key, raw = await create_api_key(
        db_session,
        organization_id=org.id,
        role=ApiKeyRole.PRODUCT_SERVICE.value,
    )
    await db_session.commit()

    digest = hash_api_key(raw)
    await authenticate(db_session, raw)
    assert get_cached_auth_context(digest) is not None
    assert get_cached_auth_context(digest).api_key_id == api_key.id  # type: ignore[union-attr]

    await revoke_api_key(
        db_session,
        organization_id=org.id,
        actor_key_id=api_key.id,
    )
    await db_session.commit()
    assert get_cached_auth_context(digest) is None
