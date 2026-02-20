"""Integration: invoice list/get with tenant isolation."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from billing_platform.domain.models.api_key import ApiKeyRole
from billing_platform.services.api_keys import create_api_key
from billing_platform.services.invoices import add_line_item, create_draft_invoice
from billing_platform.services.organizations import create_organization


@pytest.mark.integration
async def test_invoice_list_tenant_isolation_and_totals(
    api_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Org A sees its invoices with computed totals; cannot access org B."""
    _, admin_raw = await create_api_key(
        db_session,
        organization_id=None,
        role=ApiKeyRole.PLATFORM_ADMIN.value,
    )
    org_a = await create_organization(
        db_session,
        name="Invoice Org A",
        external_id="ext-inv-a",
        idempotency_key="idem-inv-a",
    )
    org_b = await create_organization(
        db_session,
        name="Invoice Org B",
        external_id="ext-inv-b",
        idempotency_key="idem-inv-b",
    )
    period_start = datetime(2026, 2, 1, tzinfo=UTC)
    period_end = datetime(2026, 2, 28, 23, 59, 59, tzinfo=UTC)

    invoice_a = await create_draft_invoice(
        db_session,
        organization_id=org_a.id,
        currency="usd",
        period_start=period_start,
        period_end=period_end,
        idempotency_key="inv-a-1",
    )
    await add_line_item(
        db_session,
        invoice_id=invoice_a.id,
        description="API calls",
        quantity=3,
        unit_amount_cents=100,
        feature_key="api_calls",
    )
    await create_draft_invoice(
        db_session,
        organization_id=org_b.id,
        currency="usd",
        period_start=period_start,
        period_end=period_end,
        idempotency_key="inv-b-1",
    )
    _, key_a_raw = await create_api_key(
        db_session,
        organization_id=org_a.id,
        role=ApiKeyRole.PRODUCT_SERVICE.value,
    )
    await db_session.commit()

    key_a_headers = {"Authorization": f"Bearer {key_a_raw}"}
    admin_headers = {"Authorization": f"Bearer {admin_raw}"}

    list_a = await api_client.get(
        f"/v1/organizations/{org_a.public_id}/invoices",
        headers=key_a_headers,
    )
    assert list_a.status_code == 200
    body_a = list_a.json()
    assert len(body_a) == 1
    assert body_a[0]["public_id"] == str(invoice_a.public_id)
    assert body_a[0]["total_amount_cents"] == 300
    assert "id" not in body_a[0]
    assert "organization_id" not in body_a[0]

    detail_a = await api_client.get(
        f"/v1/invoices/{invoice_a.public_id}",
        headers=key_a_headers,
    )
    assert detail_a.status_code == 200
    detail_body = detail_a.json()
    assert detail_body["total_amount_cents"] == 300
    assert len(detail_body["line_items"]) == 1
    assert detail_body["line_items"][0]["amount_cents"] == 300
    assert "invoice_id" not in detail_body["line_items"][0]

    cross_list = await api_client.get(
        f"/v1/organizations/{org_b.public_id}/invoices",
        headers=key_a_headers,
    )
    assert cross_list.status_code == 403

    admin_list_b = await api_client.get(
        f"/v1/organizations/{org_b.public_id}/invoices",
        headers=admin_headers,
    )
    assert admin_list_b.status_code == 200
    assert len(admin_list_b.json()) == 1
