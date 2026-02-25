"""Reconciliation mismatch seed helper (adapted from seed_recon_mismatch.py)."""

from __future__ import annotations

from seed_prod_like_webhooks import ensure_mock_stripe_invoice

from billing_platform.config import Settings
from billing_platform.domain.models.ledger import LedgerEntryType
from billing_platform.services.ledger import LedgerService
from billing_platform.services.organizations import create_organization

STRIPE_AMOUNT_CENTS = 1000
PLATFORM_AMOUNT_CENTS = 900


async def seed_recon_mismatch_org(session, settings: Settings, recon_idx: int) -> str:
    """Create one recon mismatch org; returns invoice external id."""
    org_idx_key = f"{recon_idx:04d}"
    invoice_id = f"in_pl_recon_{org_idx_key}"
    org = await create_organization(
        session,
        name=f"PL Recon Org {org_idx_key}",
        external_id=f"ext_pl_recon_{org_idx_key}",
        idempotency_key=f"pl_recon_org_{org_idx_key}",
        billing_email=f"recon-{org_idx_key}@prod-like.example.com",
        metadata={
            "seed_slice": "prod_like",
            "profile": "recon",
            "recon_idx": recon_idx,
        },
    )
    await ensure_mock_stripe_invoice(
        settings,
        invoice_id=invoice_id,
        amount_cents=STRIPE_AMOUNT_CENTS,
        external_subscription_id=f"sub_pl_recon_{org_idx_key}",
    )
    await LedgerService.post(
        session,
        organization_id=org.id,
        entry_type=LedgerEntryType.invoice_paid.value,
        amount_cents=PLATFORM_AMOUNT_CENTS,
        currency="USD",
        idempotency_key=f"pl_recon_ledger_{org_idx_key}",
        correlation_id=f"pl-recon-{org_idx_key}",
        metadata={"invoice_external_id": invoice_id},
    )
    return invoice_id
