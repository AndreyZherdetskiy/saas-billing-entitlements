"""Unit tests for organization service."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from billing_platform.services.organizations import (
    create_organization,
    get_organization_by_public_id,
    update_organization_metadata,
)


@pytest.mark.asyncio
async def test_create_organization_returns_existing_by_idempotency_key(
    db_session: AsyncSession,
) -> None:
    idem = f"idem-org-dup-{uuid.uuid4().hex[:8]}"
    external = f"ext-dup-{uuid.uuid4().hex[:8]}"
    first = await create_organization(
        db_session,
        name="First Name",
        external_id=external,
        idempotency_key=idem,
    )
    second = await create_organization(
        db_session,
        name="Second Name",
        external_id=f"other-{uuid.uuid4().hex[:8]}",
        idempotency_key=idem,
    )
    assert first.id == second.id
    assert second.name == "First Name"


@pytest.mark.asyncio
async def test_create_organization_returns_existing_by_external_id(
    db_session: AsyncSession,
) -> None:
    external = f"ext-shared-{uuid.uuid4().hex[:8]}"
    first = await create_organization(
        db_session,
        name="External Org",
        external_id=external,
        idempotency_key=f"idem-a-{uuid.uuid4().hex[:8]}",
    )
    second = await create_organization(
        db_session,
        name="External Org Retry",
        external_id=external,
        idempotency_key=f"idem-b-{uuid.uuid4().hex[:8]}",
    )
    assert first.id == second.id


@pytest.mark.asyncio
async def test_get_organization_by_public_id_accepts_string(
    db_session: AsyncSession,
) -> None:
    org = await create_organization(
        db_session,
        name="Public Id Org",
        external_id=f"ext-pub-{uuid.uuid4().hex[:8]}",
        idempotency_key=f"idem-pub-{uuid.uuid4().hex[:8]}",
    )
    await db_session.commit()

    loaded = await get_organization_by_public_id(db_session, str(org.public_id))
    assert loaded is not None
    assert loaded.id == org.id


@pytest.mark.asyncio
async def test_update_organization_metadata(db_session: AsyncSession) -> None:
    org = await create_organization(
        db_session,
        name="Patch Org",
        external_id=f"ext-patch-{uuid.uuid4().hex[:8]}",
        idempotency_key=f"idem-patch-{uuid.uuid4().hex[:8]}",
        billing_email="old@example.test",
    )
    updated = await update_organization_metadata(
        db_session,
        org,
        name="Patched Name",
        billing_email="new@example.test",
        metadata={"tier": "pro"},
    )
    assert updated.name == "Patched Name"
    assert updated.billing_email == "new@example.test"
    assert updated.metadata_ == {"tier": "pro"}
