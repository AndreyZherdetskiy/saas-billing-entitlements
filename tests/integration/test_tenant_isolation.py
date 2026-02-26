"""Integration: cross-tenant organization access is denied."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from billing_platform.domain.models.api_key import ApiKeyRole
from billing_platform.services.api_keys import create_api_key
from billing_platform.services.catalog import create_plan, create_product, publish_plan
from billing_platform.services.organizations import create_organization
from billing_platform.services.subscriptions import create_subscription


@pytest.mark.integration
async def test_tenant_isolation_cross_org_get_returns_403(
    api_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Org A API key cannot GET org B."""
    _, admin_raw = await create_api_key(
        db_session,
        organization_id=None,
        role=ApiKeyRole.PLATFORM_ADMIN.value,
    )
    org_a = await create_organization(
        db_session,
        name="Org A",
        external_id="ext-org-a",
        idempotency_key="idem-a",
    )
    org_b = await create_organization(
        db_session,
        name="Org B",
        external_id="ext-org-b",
        idempotency_key="idem-b",
    )
    _, key_a_raw = await create_api_key(
        db_session,
        organization_id=org_a.id,
        role=ApiKeyRole.PRODUCT_SERVICE.value,
    )
    await db_session.commit()

    admin_headers = {"Authorization": f"Bearer {admin_raw}"}
    key_a_headers = {"Authorization": f"Bearer {key_a_raw}"}

    own_response = await api_client.get(
        f"/v1/organizations/{org_a.public_id}",
        headers=key_a_headers,
    )
    assert own_response.status_code == 200
    assert own_response.json()["public_id"] == str(org_a.public_id)
    assert "id" not in own_response.json()

    cross_response = await api_client.get(
        f"/v1/organizations/{org_b.public_id}",
        headers=key_a_headers,
    )
    assert cross_response.status_code == 403

    admin_cross = await api_client.get(
        f"/v1/organizations/{org_b.public_id}",
        headers=admin_headers,
    )
    assert admin_cross.status_code == 200


@pytest.mark.integration
async def test_tenant_isolation_cross_org_subscription_get_returns_403(
    api_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Org A API key cannot GET org B subscription by public id."""
    org_a = await create_organization(
        db_session,
        name="Sub Tenant Org A",
        external_id="ext-sub-tenant-a",
        idempotency_key="idem-sub-tenant-a",
    )
    org_b = await create_organization(
        db_session,
        name="Sub Tenant Org B",
        external_id="ext-sub-tenant-b",
        idempotency_key="idem-sub-tenant-b",
    )
    product = await create_product(db_session, key="tenant_sub_prod", name="Tenant Sub Product")
    plan = await create_plan(
        db_session,
        product_id=product.id,
        key="tenant_sub_plan",
        billing_interval="month",
        trial_days=0,
    )
    await publish_plan(db_session, plan.id)
    subscription_b = await create_subscription(
        db_session,
        organization_id=org_b.id,
        plan_id=plan.id,
        idempotency_key="idem-sub-tenant-b-create",
    )
    _, key_a_raw = await create_api_key(
        db_session,
        organization_id=org_a.id,
        role=ApiKeyRole.PRODUCT_SERVICE.value,
    )
    await db_session.commit()

    key_a_headers = {"Authorization": f"Bearer {key_a_raw}"}

    cross_response = await api_client.get(
        f"/v1/subscriptions/{subscription_b.public_id}",
        headers=key_a_headers,
    )
    assert cross_response.status_code == 403

    list_cross = await api_client.get(
        f"/v1/organizations/{org_b.public_id}/subscriptions",
        headers=key_a_headers,
    )
    assert list_cross.status_code == 403


@pytest.mark.integration
async def test_tenant_isolation_cross_org_usage_get_returns_403(
    api_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Org A API key cannot GET org B usage aggregates."""
    org_a = await create_organization(
        db_session,
        name="Usage Tenant Org A",
        external_id="ext-usage-tenant-a",
        idempotency_key="idem-usage-tenant-a",
    )
    org_b = await create_organization(
        db_session,
        name="Usage Tenant Org B",
        external_id="ext-usage-tenant-b",
        idempotency_key="idem-usage-tenant-b",
    )
    product = await create_product(
        db_session,
        key="tenant_usage_prod",
        name="Tenant Usage Product",
    )
    plan = await create_plan(
        db_session,
        product_id=product.id,
        key="tenant_usage_plan",
        billing_interval="month",
        trial_days=0,
    )
    await publish_plan(db_session, plan.id)
    await create_subscription(
        db_session,
        organization_id=org_b.id,
        plan_id=plan.id,
        idempotency_key="idem-usage-tenant-b-create",
        metadata={},
    )
    _, key_a_raw = await create_api_key(
        db_session,
        organization_id=org_a.id,
        role=ApiKeyRole.PRODUCT_SERVICE.value,
    )
    await db_session.commit()

    key_a_headers = {"Authorization": f"Bearer {key_a_raw}"}

    cross_response = await api_client.get(
        f"/v1/organizations/{org_b.public_id}/usage",
        headers=key_a_headers,
    )
    assert cross_response.status_code == 403


@pytest.mark.integration
async def test_tenant_isolation_cross_org_evaluate_returns_403(
    api_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Org A API key cannot evaluate org B (no existence leak via 404)."""
    org_a = await create_organization(
        db_session,
        name="Eval Tenant Org A",
        external_id="ext-eval-tenant-a",
        idempotency_key="idem-eval-tenant-a",
    )
    org_b = await create_organization(
        db_session,
        name="Eval Tenant Org B",
        external_id="ext-eval-tenant-b",
        idempotency_key="idem-eval-tenant-b",
    )
    _, key_a_raw = await create_api_key(
        db_session,
        organization_id=org_a.id,
        role=ApiKeyRole.PRODUCT_SERVICE.value,
    )
    await db_session.commit()

    cross_response = await api_client.post(
        "/v1/entitlements/evaluate",
        json={
            "organization_public_id": str(org_b.public_id),
            "checks": [{"feature_key": "api_calls", "quantity": 1}],
        },
        headers={"Authorization": f"Bearer {key_a_raw}"},
    )
    assert cross_response.status_code == 403
    assert "cross-tenant" in cross_response.json()["detail"]
