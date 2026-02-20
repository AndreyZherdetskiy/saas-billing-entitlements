"""Sync local invoices to mock Stripe after domain commit (ADR-005)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from billing_platform.domain.models.invoice import Invoice
from billing_platform.domain.models.organization import Organization
from billing_platform.integrations.mock_stripe.client import MockStripeClient
from billing_platform.integrations.payment_provider import PaymentProviderPort
from billing_platform.services.invoices import InvoiceNotFoundError


async def sync_invoice_to_mock_stripe(
    session: AsyncSession,
    *,
    invoice_id: int,
    stripe_client: PaymentProviderPort | None = None,
) -> str:
    """Push invoice to mock Stripe; update only sync metadata columns."""
    invoice = await session.get(Invoice, invoice_id)
    if invoice is None:
        raise InvoiceNotFoundError(f"invoice {invoice_id} not found")

    if invoice.external_invoice_id is not None:
        return invoice.external_invoice_id

    original_total = invoice.total_amount_cents
    client = stripe_client or MockStripeClient()

    org_result = await session.execute(
        select(Organization).where(Organization.id == invoice.organization_id)
    )
    org = org_result.scalar_one()
    email = org.billing_email or f"org-{org.public_id}@billing.local"
    customer_id = await client.create_customer(
        organization_public_id=str(org.public_id),
        email=email,
    )

    external_id = await client.create_invoice(
        customer_id=customer_id,
        amount_cents=invoice.total_amount_cents,
        currency=invoice.currency,
        idempotency_key=invoice.idempotency_key,
    )

    synced_at = datetime.now(UTC)
    await session.execute(
        update(Invoice)
        .where(Invoice.id == invoice_id)
        .values(
            external_invoice_id=external_id,
            synced_at=synced_at,
            updated_at=synced_at,
        )
    )
    await session.flush()

    refreshed = await session.get(Invoice, invoice_id)
    if refreshed is None:
        raise InvoiceNotFoundError(f"invoice {invoice_id} not found after sync")
    if refreshed.total_amount_cents != original_total:
        raise RuntimeError("invoice amount mutated during mock Stripe sync")

    return external_id
