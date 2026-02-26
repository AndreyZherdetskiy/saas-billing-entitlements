"""Integration: subscription create idempotency via Idempotency-Key header."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from billing_platform.domain.models.api_key import ApiKeyRole
from billing_platform.services.api_keys import create_api_key
from billing_platform.services.catalog import create_plan, create_product, publish_plan
from billing_platform.services.organizations import create_organization


@pytest.mark.integration
async def test_create_subscription_retry_same_idempotency_key_returns_same_subscription(
    api_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Retry with the same Idempotency-Key returns the original subscription."""
    org = await create_organization(
        db_session,
        name="Sub Idem Org",
        external_id="ext-sub-idem",
        idempotency_key="idem-org-for-sub",
    )
    product = await create_product(db_session, key="sub_prod", name="Sub Product")
    plan = await create_plan(
        db_session,
        product_id=product.id,
        key="growth",
        billing_interval="month",
        trial_days=0,
    )
    await publish_plan(db_session, plan.id)

    _, admin_raw = await create_api_key(
        db_session,
        organization_id=org.id,
        role=ApiKeyRole.PRODUCT_SERVICE.value,
    )
    await db_session.commit()

    headers = {
        "Authorization": f"Bearer {admin_raw}",
        "Idempotency-Key": "idem-create-sub-001",
    }
    payload = {
        "organization_public_id": str(org.public_id),
        "plan_id": str(plan.id),
    }

    first = await api_client.post("/v1/subscriptions", json=payload, headers=headers)
    assert first.status_code == 201
    first_body = first.json()

    retry_payload = {
        "organization_public_id": str(org.public_id),
        "plan_id": str(plan.id),
        "metadata": {"note": "retry"},
    }
    second = await api_client.post("/v1/subscriptions", json=retry_payload, headers=headers)
    assert second.status_code == 201
    second_body = second.json()

    assert second_body["public_id"] == first_body["public_id"]
    assert second_body["status"] == first_body["status"]
    assert second_body["status"] == "incomplete"
