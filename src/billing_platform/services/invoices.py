from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from billing_platform.domain.models.invoice import Invoice, InvoiceLineItem, InvoiceStatus


class InvoiceError(Exception):
    """Base invoice service error."""


class InvoiceNotFoundError(InvoiceError):
    """Referenced invoice does not exist."""


class InvoiceNotMutableError(InvoiceError):
    """Invoice amounts cannot be changed once issued."""


def line_total_cents(*, quantity: int, unit_amount_cents: int) -> int:
    """Compute line total in cents from quantity and unit price."""
    return quantity * unit_amount_cents


async def _find_invoice_by_idempotency_key(
    session: AsyncSession,
    idempotency_key: str,
) -> Invoice | None:
    result = await session.execute(
        select(Invoice).where(Invoice.idempotency_key == idempotency_key)
    )
    return result.scalar_one_or_none()


async def create_draft_invoice(
    session: AsyncSession,
    *,
    organization_id: int,
    currency: str,
    period_start: datetime,
    period_end: datetime,
    idempotency_key: str,
    subscription_id: int | None = None,
) -> Invoice:
    """Create a draft invoice idempotently."""
    existing = await _find_invoice_by_idempotency_key(session, idempotency_key)
    if existing is not None:
        return existing

    invoice = Invoice(
        organization_id=organization_id,
        subscription_id=subscription_id,
        status=InvoiceStatus.draft.value,
        currency=currency.upper(),
        period_start=period_start,
        period_end=period_end,
        total_amount_cents=0,
        idempotency_key=idempotency_key,
    )
    session.add(invoice)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        existing = await _find_invoice_by_idempotency_key(session, idempotency_key)
        if existing is not None:
            return existing
        raise
    return invoice


async def _recompute_invoice_total(session: AsyncSession, invoice_id: int) -> None:
    result = await session.execute(
        select(func.coalesce(func.sum(InvoiceLineItem.amount_cents), 0)).where(
            InvoiceLineItem.invoice_id == invoice_id
        )
    )
    total = int(result.scalar_one())
    invoice_result = await session.execute(select(Invoice).where(Invoice.id == invoice_id))
    invoice = invoice_result.scalar_one()
    invoice.total_amount_cents = total
    await session.flush()


async def add_line_item(
    session: AsyncSession,
    *,
    invoice_id: int,
    description: str,
    quantity: int,
    unit_amount_cents: int,
    feature_key: str | None,
) -> InvoiceLineItem:
    """Append a line item to a draft invoice and recompute totals."""
    invoice_result = await session.execute(select(Invoice).where(Invoice.id == invoice_id))
    invoice = invoice_result.scalar_one_or_none()
    if invoice is None:
        raise InvoiceNotFoundError(f"invoice {invoice_id} not found")
    if invoice.status != InvoiceStatus.draft.value:
        raise InvoiceNotMutableError(
            f"cannot add line items to invoice in status {invoice.status}"
        )

    amount_cents = line_total_cents(quantity=quantity, unit_amount_cents=unit_amount_cents)
    line_item = InvoiceLineItem(
        invoice_id=invoice_id,
        description=description,
        quantity=Decimal(quantity),
        unit_amount_cents=unit_amount_cents,
        amount_cents=amount_cents,
        feature_key=feature_key,
    )
    session.add(line_item)
    await session.flush()
    await _recompute_invoice_total(session, invoice_id)
    return line_item


async def get_invoice_by_public_id(
    session: AsyncSession,
    invoice_public_id: uuid.UUID,
) -> Invoice | None:
    """Load an invoice by public UUID."""
    result = await session.execute(select(Invoice).where(Invoice.public_id == invoice_public_id))
    return result.scalar_one_or_none()


async def list_invoices_for_organization(
    session: AsyncSession,
    *,
    organization_id: int,
    limit: int = 100,
    offset: int = 0,
) -> list[Invoice]:
    """List invoices for an organization, newest first."""
    result = await session.execute(
        select(Invoice)
        .where(Invoice.organization_id == organization_id)
        .order_by(Invoice.created_at.desc(), Invoice.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(result.scalars().all())


async def list_line_items_for_invoice(
    session: AsyncSession,
    *,
    invoice_id: int,
) -> list[InvoiceLineItem]:
    """List line items for an invoice in creation order."""
    result = await session.execute(
        select(InvoiceLineItem)
        .where(InvoiceLineItem.invoice_id == invoice_id)
        .order_by(InvoiceLineItem.created_at.asc(), InvoiceLineItem.id.asc())
    )
    return list(result.scalars().all())
