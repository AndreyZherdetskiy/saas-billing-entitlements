"""Integration tests for the usage batch ingest API."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from billing_platform.domain.models.api_key import ApiKeyRole
from billing_platform.services.api_keys import create_api_key
from billing_platform.services.organizations import create_organization


async def _create_org_and_key(
    session: AsyncSession,
    *,
    suffix: str,
    role: str = ApiKeyRole.PRODUCT_SERVICE.value,
) -> tuple[str, dict[str, str]]:
    organization = await create_organization(
        session,
        name=f"Usage organization {suffix}",
        external_id=f"usage-org-{suffix}",
        idempotency_key=f"usage-org-idem-{suffix}",
    )
    _, raw_key = await create_api_key(
        session,
        organization_id=organization.id,
        role=role,
    )
    await session.commit()
    return str(organization.public_id), {"Authorization": f"Bearer {raw_key}"}


def _body(organization_public_id: str, *, count: int = 1) -> dict[str, object]:
    return {
        "organization_public_id": organization_public_id,
        "events": [
            {
                "feature_key": "api_calls",
                "quantity": 1,
                "idempotency_key": f"usage-event-{index}",
            }
            for index in range(count)
        ],
    }


@pytest.mark.integration
async def test_usage_batch_happy_path(
    api_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    organization_public_id, headers = await _create_org_and_key(db_session, suffix="happy")

    response = await api_client.post(
        "/v1/usage/events/batch",
        json=_body(organization_public_id, count=2),
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json()["accepted"] == 2
    assert response.json()["duplicates"] == 0
    assert len(response.json()["usage_event_public_ids"]) == 2
    assert "id" not in response.json()


@pytest.mark.integration
async def test_usage_batch_duplicate_is_idempotent(
    api_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    organization_public_id, headers = await _create_org_and_key(db_session, suffix="duplicate")
    body = _body(organization_public_id)

    first = await api_client.post("/v1/usage/events/batch", json=body, headers=headers)
    second = await api_client.post("/v1/usage/events/batch", json=body, headers=headers)

    assert first.status_code == 200
    assert first.json()["accepted"] == 1
    assert second.status_code == 200
    assert second.json() == {
        "accepted": 0,
        "duplicates": 1,
        "usage_event_public_ids": first.json()["usage_event_public_ids"],
    }


@pytest.mark.integration
async def test_usage_batch_more_than_1000_returns_400(
    api_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    organization_public_id, headers = await _create_org_and_key(db_session, suffix="oversized")

    response = await api_client.post(
        "/v1/usage/events/batch",
        json=_body(organization_public_id, count=1001),
        headers=headers,
    )

    assert response.status_code == 400


@pytest.mark.integration
async def test_usage_batch_cross_tenant_returns_403(
    api_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    _, org_a_headers = await _create_org_and_key(db_session, suffix="tenant-a")
    org_b_public_id, _ = await _create_org_and_key(db_session, suffix="tenant-b")

    response = await api_client.post(
        "/v1/usage/events/batch",
        json=_body(org_b_public_id),
        headers=org_a_headers,
    )

    assert response.status_code == 403


@pytest.mark.integration
async def test_usage_batch_revops_read_returns_403(
    api_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    organization_public_id, headers = await _create_org_and_key(
        db_session,
        suffix="revops-read",
        role=ApiKeyRole.REVOPS_READ.value,
    )

    response = await api_client.post(
        "/v1/usage/events/batch",
        json=_body(organization_public_id),
        headers=headers,
    )

    assert response.status_code == 403


@pytest.mark.integration
async def test_usage_batch_naive_recorded_at_returns_422(
    api_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    organization_public_id, headers = await _create_org_and_key(db_session, suffix="naive")
    body = _body(organization_public_id)
    body["events"][0]["recorded_at"] = "2026-02-16T12:00:00"

    response = await api_client.post(
        "/v1/usage/events/batch",
        json=body,
        headers=headers,
    )

    assert response.status_code == 422
