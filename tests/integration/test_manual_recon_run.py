"""Integration: manual reconciliation run detects seeded amount mismatch."""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from billing_platform.domain.models.api_key import ApiKeyRole
from billing_platform.domain.models.ledger import LedgerEntry, LedgerEntryType
from billing_platform.services.api_keys import create_api_key
from billing_platform.services.ledger import LedgerService
from billing_platform.services.organizations import create_organization

SEED_INVOICE_ID = "in_recon_seed_mismatch_001"
STRIPE_AMOUNT_CENTS = 1000
PLATFORM_AMOUNT_CENTS = 900


def _mock_stripe_invoices() -> list[dict[str, Any]]:
    return [
        {
            "id": SEED_INVOICE_ID,
            "object": "invoice",
            "status": "paid",
            "amount_due": STRIPE_AMOUNT_CENTS,
            "amount_paid": STRIPE_AMOUNT_CENTS,
            "currency": "usd",
        }
    ]


async def _seed_platform_ledger(db_session: AsyncSession) -> None:
    org = await create_organization(
        db_session,
        name="Recon Mismatch Org",
        external_id="ext-recon-mismatch",
        idempotency_key="seed-recon-org",
    )
    await LedgerService.post(
        db_session,
        organization_id=org.id,
        entry_type=LedgerEntryType.invoice_paid.value,
        amount_cents=PLATFORM_AMOUNT_CENTS,
        currency="USD",
        idempotency_key="seed-recon-ledger-entry",
        correlation_id="seed-recon",
        metadata={"invoice_external_id": SEED_INVOICE_ID},
    )
    await db_session.commit()


@pytest.mark.integration
async def test_manual_recon_run_detects_amount_mismatch(
    api_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """POST /admin/reconciliation/run records amount_mismatch for seeded data."""
    await _seed_platform_ledger(db_session)

    _, admin_raw = await create_api_key(
        db_session,
        organization_id=None,
        role=ApiKeyRole.PLATFORM_ADMIN.value,
    )
    await db_session.commit()
    headers = {
        "Authorization": f"Bearer {admin_raw}",
        "Idempotency-Key": f"recon-manual-{uuid.uuid4().hex}",
    }

    ledger_count_before = await db_session.scalar(select(func.count()).select_from(LedgerEntry))

    with patch(
        "billing_platform.services.reconciliation.MockStripeClient.list_invoices",
        new=AsyncMock(return_value=_mock_stripe_invoices()),
    ):
        run_resp = await api_client.post("/v1/admin/reconciliation/run", headers=headers)

    assert run_resp.status_code == 201
    run_body = run_resp.json()
    assert run_body["status"] == "completed"
    assert run_body["stats"]["discrepancy_count"] >= 1

    run_id = run_body["id"]
    disc_resp = await api_client.get(
        f"/v1/admin/reconciliation/runs/{run_id}/discrepancies",
        headers={"Authorization": f"Bearer {admin_raw}"},
    )
    assert disc_resp.status_code == 200
    discrepancies = disc_resp.json()
    amount_mismatches = [d for d in discrepancies if d["kind"] == "amount_mismatch"]
    assert len(amount_mismatches) >= 1
    mismatch = next(d for d in amount_mismatches if d["external_invoice_id"] == SEED_INVOICE_ID)
    assert mismatch["expected_amount_cents"] == STRIPE_AMOUNT_CENTS
    assert mismatch["actual_amount_cents"] == PLATFORM_AMOUNT_CENTS
    assert mismatch["delta_cents"] == STRIPE_AMOUNT_CENTS - PLATFORM_AMOUNT_CENTS

    ledger_count_after = await db_session.scalar(select(func.count()).select_from(LedgerEntry))
    assert ledger_count_after == ledger_count_before


@pytest.mark.integration
async def test_recon_rerun_same_idempotency_key_is_idempotent(
    api_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Same Idempotency-Key returns the same run without duplicating discrepancies."""
    await _seed_platform_ledger(db_session)

    _, admin_raw = await create_api_key(
        db_session,
        organization_id=None,
        role=ApiKeyRole.PLATFORM_ADMIN.value,
    )
    await db_session.commit()

    idem_key = f"recon-idem-{uuid.uuid4().hex}"
    headers = {
        "Authorization": f"Bearer {admin_raw}",
        "Idempotency-Key": idem_key,
    }

    with patch(
        "billing_platform.services.reconciliation.MockStripeClient.list_invoices",
        new=AsyncMock(return_value=_mock_stripe_invoices()),
    ):
        first = await api_client.post("/v1/admin/reconciliation/run", headers=headers)
        second = await api_client.post("/v1/admin/reconciliation/run", headers=headers)

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] == second.json()["id"]


@pytest.mark.integration
async def test_recon_run_requires_platform_admin(
    api_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Tenant API keys cannot trigger reconciliation."""
    org = await create_organization(
        db_session,
        name="Tenant Recon Org",
        external_id=f"ext-tenant-recon-{uuid.uuid4().hex[:8]}",
        idempotency_key=f"idem-tenant-recon-{uuid.uuid4().hex[:8]}",
    )
    _, tenant_raw = await create_api_key(
        db_session,
        organization_id=org.id,
        role=ApiKeyRole.PRODUCT_SERVICE.value,
    )
    await db_session.commit()

    response = await api_client.post(
        "/v1/admin/reconciliation/run",
        headers={
            "Authorization": f"Bearer {tenant_raw}",
            "Idempotency-Key": f"recon-deny-{uuid.uuid4().hex}",
        },
    )
    assert response.status_code == 403
