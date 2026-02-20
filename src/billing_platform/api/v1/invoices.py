"""Invoice read HTTP routes."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from billing_platform.api.deps import assert_tenant_access, get_auth_context
from billing_platform.api.openapi_docs import (
    AUTH_RESPONSES,
    FORBIDDEN_RESPONSE,
    NOT_FOUND_RESPONSE,
    merge_responses,
)
from billing_platform.db import get_session
from billing_platform.domain.models.invoice import Invoice, InvoiceLineItem
from billing_platform.domain.models.organization import Organization
from billing_platform.domain.models.subscription import Subscription
from billing_platform.services.api_keys import AuthContext
from billing_platform.services.invoices import (
    get_invoice_by_public_id,
    list_invoices_for_organization,
    list_line_items_for_invoice,
)
from billing_platform.services.organizations import get_organization_by_public_id

router = APIRouter(tags=["invoices"])

_ORG_PUBLIC_ID = Path(description="External UUIDv7 of the organization (not internal BIGINT id).")
_INVOICE_PUBLIC_ID = Path(description="External UUIDv7 of the invoice (not internal BIGINT id).")


class InvoiceLineItemResponse(BaseModel):
    """Public line item representation."""

    id: UUID = Field(
        description="External UUIDv7 of the line item (not internal BIGINT id).",
    )
    description: str = Field(description="Line item description.")
    quantity: str = Field(description="Quantity as a decimal string.")
    unit_amount_cents: int = Field(description="Unit price in minor currency units.")
    amount_cents: int = Field(description="Line total in minor currency units.")
    feature_key: str | None = Field(
        default=None,
        description="Metered feature key when the line is usage-based.",
    )
    created_at: datetime = Field(description="UTC timestamp when the line item was created.")


class InvoiceResponse(BaseModel):
    """Public invoice representation (no internal BIGINT id)."""

    public_id: UUID = Field(
        description="External UUIDv7 of the invoice (not internal BIGINT id).",
    )
    organization_public_id: UUID = Field(
        description="External UUIDv7 of the organization (not internal BIGINT id).",
    )
    subscription_public_id: UUID | None = Field(
        default=None,
        description="External UUIDv7 of the related subscription, if any.",
    )
    status: str = Field(description="Invoice status (e.g. draft, open, paid).")
    currency: str = Field(description="ISO 4217 currency code.")
    period_start: datetime = Field(description="Billing period start (UTC).")
    period_end: datetime = Field(description="Billing period end (UTC).")
    total_amount_cents: int = Field(description="Invoice total in minor currency units.")
    line_items: list[InvoiceLineItemResponse] = Field(description="Invoice line items.")
    created_at: datetime = Field(description="UTC timestamp when the invoice was created.")
    updated_at: datetime = Field(description="UTC timestamp of the last invoice update.")


class InvoiceSummaryResponse(BaseModel):
    """Invoice list item without line items."""

    public_id: UUID = Field(
        description="External UUIDv7 of the invoice (not internal BIGINT id).",
    )
    organization_public_id: UUID = Field(
        description="External UUIDv7 of the organization (not internal BIGINT id).",
    )
    subscription_public_id: UUID | None = Field(
        default=None,
        description="External UUIDv7 of the related subscription, if any.",
    )
    status: str = Field(description="Invoice status (e.g. draft, open, paid).")
    currency: str = Field(description="ISO 4217 currency code.")
    period_start: datetime = Field(description="Billing period start (UTC).")
    period_end: datetime = Field(description="Billing period end (UTC).")
    total_amount_cents: int = Field(description="Invoice total in minor currency units.")
    created_at: datetime = Field(description="UTC timestamp when the invoice was created.")
    updated_at: datetime = Field(description="UTC timestamp of the last invoice update.")


def _line_item_to_response(line_item: InvoiceLineItem) -> InvoiceLineItemResponse:
    return InvoiceLineItemResponse(
        id=line_item.id,
        description=line_item.description,
        quantity=str(line_item.quantity),
        unit_amount_cents=line_item.unit_amount_cents,
        amount_cents=line_item.amount_cents,
        feature_key=line_item.feature_key,
        created_at=line_item.created_at,
    )


async def _load_organization_by_id(
    session: AsyncSession,
    organization_id: int,
) -> Organization | None:
    result = await session.execute(select(Organization).where(Organization.id == organization_id))
    return result.scalar_one_or_none()


async def _subscription_public_id(
    session: AsyncSession,
    subscription_id: int | None,
) -> UUID | None:
    if subscription_id is None:
        return None
    result = await session.execute(
        select(Subscription.public_id).where(Subscription.id == subscription_id)
    )
    return result.scalar_one_or_none()


async def _to_summary_response(
    session: AsyncSession,
    invoice: Invoice,
    organization: Organization,
) -> InvoiceSummaryResponse:
    return InvoiceSummaryResponse(
        public_id=invoice.public_id,
        organization_public_id=organization.public_id,
        subscription_public_id=await _subscription_public_id(session, invoice.subscription_id),
        status=invoice.status,
        currency=invoice.currency,
        period_start=invoice.period_start,
        period_end=invoice.period_end,
        total_amount_cents=invoice.total_amount_cents,
        created_at=invoice.created_at,
        updated_at=invoice.updated_at,
    )


async def _to_detail_response(
    session: AsyncSession,
    invoice: Invoice,
    organization: Organization,
) -> InvoiceResponse:
    line_items = await list_line_items_for_invoice(session, invoice_id=invoice.id)
    return InvoiceResponse(
        public_id=invoice.public_id,
        organization_public_id=organization.public_id,
        subscription_public_id=await _subscription_public_id(session, invoice.subscription_id),
        status=invoice.status,
        currency=invoice.currency,
        period_start=invoice.period_start,
        period_end=invoice.period_end,
        total_amount_cents=invoice.total_amount_cents,
        line_items=[_line_item_to_response(item) for item in line_items],
        created_at=invoice.created_at,
        updated_at=invoice.updated_at,
    )


@router.get(
    "/organizations/{organization_public_id}/invoices",
    response_model=list[InvoiceSummaryResponse],
    summary="List organization invoices",
    description=(
        "Returns invoice summaries (status, period, total) without line items "
        "for an organization. "
        "Tenant keys may only list their own organization; platform_admin may list any. "
        "Paginated via limit (max 500) and offset."
    ),
    responses=merge_responses(AUTH_RESPONSES, FORBIDDEN_RESPONSE, NOT_FOUND_RESPONSE),
)
async def list_organization_invoices(
    organization_public_id: Annotated[UUID, _ORG_PUBLIC_ID],
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[InvoiceSummaryResponse]:
    organization = await get_organization_by_public_id(session, organization_public_id)
    if organization is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="organization not found")
    assert_tenant_access(ctx, organization)

    invoices = await list_invoices_for_organization(
        session,
        organization_id=organization.id,
        limit=limit,
        offset=offset,
    )
    return [await _to_summary_response(session, inv, organization) for inv in invoices]


@router.get(
    "/invoices/{invoice_public_id}",
    response_model=InvoiceResponse,
    summary="Get invoice",
    description=(
        "Returns full invoice detail including line items by public UUID. "
        "Tenant keys may only read invoices for their organization; platform_admin may read any."
    ),
    responses=merge_responses(AUTH_RESPONSES, FORBIDDEN_RESPONSE, NOT_FOUND_RESPONSE),
)
async def get_invoice(
    invoice_public_id: Annotated[UUID, _INVOICE_PUBLIC_ID],
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> InvoiceResponse:
    invoice = await get_invoice_by_public_id(session, invoice_public_id)
    if invoice is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="invoice not found")

    organization = await _load_organization_by_id(session, invoice.organization_id)
    if organization is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="organization not found")
    assert_tenant_access(ctx, organization)

    return await _to_detail_response(session, invoice, organization)
