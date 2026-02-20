"""Integration: POST /v1/organizations idempotency via Idempotency-Key header."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from billing_platform.domain.models.api_key import ApiKeyRole
from billing_platform.services.api_keys import create_api_key


@pytest.mark.integration
async def test_create_organization_retry_same_idempotency_key_returns_same_org(
    api_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Retry with the same Idempotency-Key returns the original organization."""
    _, admin_raw = await create_api_key(
        db_session,
        organization_id=None,
        role=ApiKeyRole.PLATFORM_ADMIN.value,
    )
    await db_session.commit()

    headers = {
        "Authorization": f"Bearer {admin_raw}",
        "Idempotency-Key": "idem-create-org-001",
    }
    payload = {
        "name": "Acme Corp",
        "external_id": "ext-acme-001",
        "billing_email": "billing@acme.test",
    }

    first = await api_client.post("/v1/organizations", json=payload, headers=headers)
    assert first.status_code == 201
    first_body = first.json()

    retry_payload = {
        "name": "Different Name",
        "external_id": "ext-different",
        "billing_email": "other@acme.test",
    }
    second = await api_client.post("/v1/organizations", json=retry_payload, headers=headers)
    assert second.status_code == 201
    second_body = second.json()

    assert second_body["public_id"] == first_body["public_id"]
    assert second_body["external_id"] == first_body["external_id"]
    assert second_body["name"] == first_body["name"]


@pytest.mark.integration
async def test_create_organization_requires_idempotency_key_header(
    api_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Missing Idempotency-Key header is rejected."""
    _, admin_raw = await create_api_key(
        db_session,
        organization_id=None,
        role=ApiKeyRole.PLATFORM_ADMIN.value,
    )
    await db_session.commit()

    response = await api_client.post(
        "/v1/organizations",
        json={"name": "No Key", "external_id": "ext-no-key"},
        headers={"Authorization": f"Bearer {admin_raw}"},
    )
    assert response.status_code == 422
