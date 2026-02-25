"""Integration: API key rotation overlap window and revoke."""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from billing_platform.domain.models.api_key import ApiKey, ApiKeyRole
from billing_platform.services.api_keys import (
    authenticate,
    create_api_key,
    revoke_api_key,
    rotate_api_key,
)
from billing_platform.services.organizations import create_organization


@pytest.mark.integration
@pytest.mark.asyncio
async def test_rotate_overlap_then_revoke_old_key(
    db_session: AsyncSession,
) -> None:
    """After rotate, old and new both authenticate until old is revoked."""
    org = await create_organization(
        db_session,
        name="Rotate Org",
        external_id=f"ext-rot-{uuid.uuid4().hex[:8]}",
        idempotency_key=f"idem-rot-{uuid.uuid4().hex[:8]}",
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

    old_ctx = await authenticate(db_session, old_raw)
    new_ctx = await authenticate(db_session, new_raw)
    assert old_ctx.api_key_id == old_key.id
    assert new_ctx.api_key_id == new_key.id
    assert old_key.id != new_key.id

    await revoke_api_key(
        db_session,
        organization_id=org.id,
        actor_key_id=old_key.id,
    )
    await db_session.commit()

    with pytest.raises(ValueError, match="invalid or expired"):
        await authenticate(db_session, old_raw)

    still_new = await authenticate(db_session, new_raw)
    assert still_new.api_key_id == new_key.id


@pytest.mark.integration
@pytest.mark.asyncio
async def test_rotate_does_not_store_raw_secret_in_db(
    db_session: AsyncSession,
) -> None:
    """Only SHA-256 hex is persisted; raw secret never stored."""
    org = await create_organization(
        db_session,
        name="Hash Org",
        external_id=f"ext-hash-{uuid.uuid4().hex[:8]}",
        idempotency_key=f"idem-hash-{uuid.uuid4().hex[:8]}",
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

    result = await db_session.execute(select(ApiKey))
    keys = result.scalars().all()
    for key in keys:
        assert len(key.key_hash) == 64
        assert not key.key_hash.startswith("$2")
        assert old_raw not in key.key_hash
        assert new_raw not in key.key_hash
        assert old_raw != key.key_hash
        assert new_raw != key.key_hash

    assert new_raw.startswith("bp_")
    assert new_key.key_prefix == new_raw[:8]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_rotate_via_admin_api_self_service(
    api_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Org key can rotate itself via admin API; overlap then revoke."""
    org = await create_organization(
        db_session,
        name="Admin Rotate Org",
        external_id=f"ext-ar-{uuid.uuid4().hex[:8]}",
        idempotency_key=f"idem-ar-{uuid.uuid4().hex[:8]}",
    )
    old_key, old_raw = await create_api_key(
        db_session,
        organization_id=org.id,
        role=ApiKeyRole.PRODUCT_SERVICE.value,
    )
    await db_session.commit()

    headers = {"Authorization": f"Bearer {old_raw}"}
    with patch("billing_platform.api.v1.admin.api_keys.logger") as mock_logger:
        rotate_resp = await api_client.post(
            f"/v1/admin/api-keys/{old_key.id}/rotate",
            headers=headers,
        )
        assert rotate_resp.status_code == 201
        body = rotate_resp.json()
        new_raw = body["raw_key"]
        new_key_id = body["id"]
        assert new_raw.startswith("bp_")
        for call in mock_logger.info.call_args_list + mock_logger.warning.call_args_list:
            assert new_raw not in str(call)

    old_ctx = await authenticate(db_session, old_raw)
    new_ctx = await authenticate(db_session, new_raw)
    assert old_ctx.api_key_id == old_key.id
    assert new_ctx.api_key_id == uuid.UUID(new_key_id)

    revoke_resp = await api_client.post(
        f"/v1/admin/api-keys/{old_key.id}/revoke",
        headers={"Authorization": f"Bearer {new_raw}"},
    )
    assert revoke_resp.status_code == 200
    assert revoke_resp.json()["revoked_at"] is not None

    with pytest.raises(ValueError, match="invalid or expired"):
        await authenticate(db_session, old_raw)
    await authenticate(db_session, new_raw)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_platform_admin_can_rotate_org_key(
    api_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    org = await create_organization(
        db_session,
        name="Platform Rotate Org",
        external_id=f"ext-pr-{uuid.uuid4().hex[:8]}",
        idempotency_key=f"idem-pr-{uuid.uuid4().hex[:8]}",
    )
    org_key, org_raw = await create_api_key(
        db_session,
        organization_id=org.id,
        role=ApiKeyRole.PRODUCT_SERVICE.value,
    )
    _, admin_raw = await create_api_key(
        db_session,
        organization_id=None,
        role=ApiKeyRole.PLATFORM_ADMIN.value,
    )
    await db_session.commit()

    rotate_resp = await api_client.post(
        f"/v1/admin/api-keys/{org_key.id}/rotate",
        headers={"Authorization": f"Bearer {admin_raw}"},
    )
    assert rotate_resp.status_code == 201
    new_raw = rotate_resp.json()["raw_key"]

    await authenticate(db_session, org_raw)
    await authenticate(db_session, new_raw)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_support_read_cannot_revoke_other_role_key(
    api_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Read-only support_read must not revoke keys of other roles (F-1)."""
    org = await create_organization(
        db_session,
        name="Support Read Revoke Org",
        external_id=f"ext-sr-{uuid.uuid4().hex[:8]}",
        idempotency_key=f"idem-sr-{uuid.uuid4().hex[:8]}",
    )
    product_key, _product_raw = await create_api_key(
        db_session,
        organization_id=org.id,
        role=ApiKeyRole.PRODUCT_SERVICE.value,
    )
    _support_key, support_raw = await create_api_key(
        db_session,
        organization_id=org.id,
        role=ApiKeyRole.SUPPORT_READ.value,
    )
    await db_session.commit()

    revoke_resp = await api_client.post(
        f"/v1/admin/api-keys/{product_key.id}/revoke",
        headers={"Authorization": f"Bearer {support_raw}"},
    )
    assert revoke_resp.status_code == 403
    assert "read-only" in revoke_resp.json()["detail"]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_org_cannot_rotate_other_org_api_key(
    api_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Cross-tenant: org A credentials cannot rotate org B's API key (E4-03)."""
    org_a = await create_organization(
        db_session,
        name="Org A Rotate",
        external_id=f"ext-a-{uuid.uuid4().hex[:8]}",
        idempotency_key=f"idem-a-{uuid.uuid4().hex[:8]}",
    )
    org_b = await create_organization(
        db_session,
        name="Org B Rotate",
        external_id=f"ext-b-{uuid.uuid4().hex[:8]}",
        idempotency_key=f"idem-b-{uuid.uuid4().hex[:8]}",
    )
    _org_a_key, org_a_raw = await create_api_key(
        db_session,
        organization_id=org_a.id,
        role=ApiKeyRole.PRODUCT_SERVICE.value,
    )
    org_b_key, _org_b_raw = await create_api_key(
        db_session,
        organization_id=org_b.id,
        role=ApiKeyRole.PRODUCT_SERVICE.value,
    )
    await db_session.commit()

    rotate_resp = await api_client.post(
        f"/v1/admin/api-keys/{org_b_key.id}/rotate",
        headers={"Authorization": f"Bearer {org_a_raw}"},
    )
    assert rotate_resp.status_code == 403
    assert "rotate" in rotate_resp.json()["detail"].lower()
