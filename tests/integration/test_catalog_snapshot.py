"""Integration: catalog publish and GET /catalog/snapshot."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from billing_platform.domain.models.api_key import ApiKeyRole
from billing_platform.services.api_keys import create_api_key


@pytest.mark.integration
async def test_catalog_snapshot_after_publish_includes_plan_features(
    api_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    _, admin_raw = await create_api_key(
        db_session,
        organization_id=None,
        role=ApiKeyRole.PLATFORM_ADMIN.value,
    )
    await db_session.commit()

    headers = {"Authorization": f"Bearer {admin_raw}"}

    product_resp = await api_client.post(
        "/v1/products",
        json={"key": "core_api", "name": "Core API"},
        headers=headers,
    )
    assert product_resp.status_code == 201
    product_id = product_resp.json()["id"]

    feature_resp = await api_client.post(
        "/v1/features",
        json={"key": "api_calls", "feature_type": "quota", "default_limit": 1000},
        headers=headers,
    )
    assert feature_resp.status_code == 201
    feature_id = feature_resp.json()["id"]

    plan_resp = await api_client.post(
        "/v1/plans",
        json={
            "product_id": product_id,
            "key": "pro",
            "billing_interval": "month",
            "trial_days": 14,
        },
        headers=headers,
    )
    assert plan_resp.status_code == 201
    plan_body = plan_resp.json()
    plan_id = plan_body["id"]
    assert plan_body["published_at"] is None

    bind_resp = await api_client.put(
        f"/v1/plans/{plan_id}/features",
        json={
            "features": [
                {
                    "feature_id": feature_id,
                    "limit_value": 5000,
                    "is_enabled": True,
                    "enforcement_mode": "hard",
                }
            ]
        },
        headers=headers,
    )
    assert bind_resp.status_code == 200

    price_resp = await api_client.post(
        "/v1/prices",
        json={
            "plan_id": plan_id,
            "unit_amount_cents": 2900,
            "pricing_model": "flat",
        },
        headers=headers,
    )
    assert price_resp.status_code == 201

    publish_resp = await api_client.post(f"/v1/plans/{plan_id}/publish", headers=headers)
    assert publish_resp.status_code == 200
    assert publish_resp.json()["published_at"] is not None

    snapshot_resp = await api_client.get("/v1/catalog/snapshot", headers=headers)
    assert snapshot_resp.status_code == 200
    snapshot = snapshot_resp.json()

    assert len(snapshot["plans"]) == 1
    assert snapshot["plans"][0]["id"] == plan_id
    assert len(snapshot["plan_features"]) == 1
    assert snapshot["plan_features"][0]["plan_id"] == plan_id
    assert snapshot["plan_features"][0]["feature_id"] == feature_id
    assert snapshot["plan_features"][0]["limit_value"] == 5000
    assert len(snapshot["prices"]) == 1
    assert len(snapshot["products"]) == 1


@pytest.mark.integration
async def test_draft_plan_not_in_snapshot(
    api_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    _, admin_raw = await create_api_key(
        db_session,
        organization_id=None,
        role=ApiKeyRole.PLATFORM_ADMIN.value,
    )
    await db_session.commit()

    headers = {"Authorization": f"Bearer {admin_raw}"}

    product_resp = await api_client.post(
        "/v1/products",
        json={"key": "draft_only", "name": "Draft Only"},
        headers=headers,
    )
    product_id = product_resp.json()["id"]

    await api_client.post(
        "/v1/plans",
        json={"product_id": product_id, "key": "beta", "billing_interval": "month"},
        headers=headers,
    )

    snapshot_resp = await api_client.get("/v1/catalog/snapshot", headers=headers)
    assert snapshot_resp.status_code == 200
    assert snapshot_resp.json()["plans"] == []
