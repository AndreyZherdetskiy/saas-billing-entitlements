"""Billing period close: aggregates → invoice lines → ledger usage_charge → outbox."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from billing_platform.domain.models.invoice import Invoice, InvoiceStatus
from billing_platform.domain.models.ledger import LedgerEntry, LedgerEntryType
from billing_platform.domain.models.organization import Organization
from billing_platform.domain.models.price import Price
from billing_platform.domain.models.usage_aggregate import UsageAggregate
from billing_platform.services.invoices import add_line_item, create_draft_invoice
from billing_platform.services.ledger import LedgerService
from billing_platform.services.outbox_hooks import enqueue_outbox
from billing_platform.services.subscriptions import get_primary_subscription

USAGE_PERIOD_CLOSED_EVENT = "usage.period_closed"


class PeriodCloseError(Exception):
    """Base period close service error."""


class SubscriptionRequiredError(PeriodCloseError):
    """Organization has no billable subscription for period close."""


@dataclass(frozen=True)
class PeriodCloseResult:
    """Outcome of closing a billing period."""

    invoice_public_id: uuid.UUID
    invoice_id: int
    total_amount_cents: int
    ledger_entry_public_id: uuid.UUID | None


def _normalize_period_bound(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("period bounds must be timezone-aware")
    return value.astimezone(UTC)


async def _find_invoice_by_idempotency_key(
    session: AsyncSession,
    idempotency_key: str,
) -> Invoice | None:
    result = await session.execute(
        select(Invoice).where(Invoice.idempotency_key == idempotency_key)
    )
    return result.scalar_one_or_none()


async def _load_metered_prices(
    session: AsyncSession,
    plan_id: uuid.UUID,
) -> dict[str, Price]:
    result = await session.execute(
        select(Price).where(
            Price.plan_id == plan_id,
            Price.is_active.is_(True),
            Price.pricing_model == "per_unit",
            Price.metered_feature_key.is_not(None),
        )
    )
    prices = result.scalars().all()
    return {price.metered_feature_key: price for price in prices if price.metered_feature_key}


async def _snapshot_usage_by_feature(
    session: AsyncSession,
    *,
    organization_id: int,
    period_start: datetime,
    period_end: datetime,
) -> dict[str, Decimal]:
    result = await session.execute(
        select(
            UsageAggregate.feature_key,
            func.coalesce(func.sum(UsageAggregate.quantity), 0),
        )
        .where(
            UsageAggregate.organization_id == organization_id,
            UsageAggregate.hour_start >= period_start,
            UsageAggregate.hour_start < period_end,
        )
        .group_by(UsageAggregate.feature_key)
    )
    usage: dict[str, Decimal] = {}
    for feature_key, quantity in result:
        qty = Decimal(quantity)
        if qty > 0:
            usage[feature_key] = qty
    return usage


async def _enqueue_period_closed(
    session: AsyncSession,
    *,
    organization_id: int,
    subscription_public_id: uuid.UUID,
    invoice: Invoice,
    period_start: datetime,
    period_end: datetime,
    usage_by_feature: dict[str, Decimal],
    idempotency_key: str,
) -> None:
    org_result = await session.execute(
        select(Organization.public_id).where(Organization.id == organization_id)
    )
    org_public_id = org_result.scalar_one()

    payload: dict[str, object] = {
        "organization_public_id": str(org_public_id),
        "subscription_public_id": str(subscription_public_id),
        "invoice_public_id": str(invoice.public_id),
        "period_start": period_start.isoformat().replace("+00:00", "Z"),
        "period_end": period_end.isoformat().replace("+00:00", "Z"),
        "total_amount_cents": invoice.total_amount_cents,
        "usage_quantities": {key: str(value) for key, value in usage_by_feature.items()},
    }

    await enqueue_outbox(
        session,
        aggregate_type="usage",
        aggregate_id=str(invoice.public_id),
        event_type=USAGE_PERIOD_CLOSED_EVENT,
        payload=payload,
        idempotency_key=f"{idempotency_key}:usage.period_closed",
        partition_key=str(organization_id),
    )


async def _result_from_existing_invoice(
    session: AsyncSession,
    invoice: Invoice,
) -> PeriodCloseResult:
    ledger_result = await session.execute(
        select(LedgerEntry)
        .where(
            LedgerEntry.invoice_id == invoice.id,
            LedgerEntry.entry_type == LedgerEntryType.usage_charge.value,
        )
        .limit(1)
    )
    ledger_entry = ledger_result.scalar_one_or_none()
    return PeriodCloseResult(
        invoice_public_id=invoice.public_id,
        invoice_id=invoice.id,
        total_amount_cents=invoice.total_amount_cents,
        ledger_entry_public_id=ledger_entry.public_id if ledger_entry is not None else None,
    )


async def close_billing_period(
    session: AsyncSession,
    *,
    organization_id: int,
    period_start: datetime,
    period_end: datetime,
    idempotency_key: str,
) -> PeriodCloseResult:
    """Close a billing period idempotently in the caller's transaction."""
    normalized_start = _normalize_period_bound(period_start)
    normalized_end = _normalize_period_bound(period_end)

    existing_invoice = await _find_invoice_by_idempotency_key(session, idempotency_key)
    if existing_invoice is not None:
        return await _result_from_existing_invoice(session, existing_invoice)

    subscription = await get_primary_subscription(session, organization_id)
    if subscription is None:
        raise SubscriptionRequiredError(f"no subscription for organization {organization_id}")

    metered_prices = await _load_metered_prices(session, subscription.plan_id)
    usage_by_feature = await _snapshot_usage_by_feature(
        session,
        organization_id=organization_id,
        period_start=normalized_start,
        period_end=normalized_end,
    )

    currency = "USD"
    for price in metered_prices.values():
        currency = price.currency
        break

    invoice = await create_draft_invoice(
        session,
        organization_id=organization_id,
        currency=currency,
        period_start=normalized_start,
        period_end=normalized_end,
        idempotency_key=idempotency_key,
        subscription_id=subscription.id,
    )

    for feature_key, quantity in sorted(usage_by_feature.items()):
        metered_price = metered_prices.get(feature_key)
        if metered_price is None:
            continue
        await add_line_item(
            session,
            invoice_id=invoice.id,
            description=f"Usage: {feature_key}",
            quantity=int(quantity),
            unit_amount_cents=metered_price.unit_amount_cents,
            feature_key=feature_key,
        )

    invoice.status = InvoiceStatus.open.value
    await session.flush()

    ledger_entry = await LedgerService.post(
        session,
        organization_id=organization_id,
        entry_type=LedgerEntryType.usage_charge.value,
        amount_cents=invoice.total_amount_cents,
        currency=invoice.currency,
        idempotency_key=f"{idempotency_key}:usage_charge",
        correlation_id=idempotency_key,
        subscription_id=subscription.id,
        invoice_id=invoice.id,
        quantity=sum(usage_by_feature.values(), Decimal(0)),
        metadata={
            "invoice_public_id": str(invoice.public_id),
            "period_start": normalized_start.isoformat().replace("+00:00", "Z"),
            "period_end": normalized_end.isoformat().replace("+00:00", "Z"),
        },
    )

    await _enqueue_period_closed(
        session,
        organization_id=organization_id,
        subscription_public_id=subscription.public_id,
        invoice=invoice,
        period_start=normalized_start,
        period_end=normalized_end,
        usage_by_feature=usage_by_feature,
        idempotency_key=idempotency_key,
    )

    return PeriodCloseResult(
        invoice_public_id=invoice.public_id,
        invoice_id=invoice.id,
        total_amount_cents=invoice.total_amount_cents,
        ledger_entry_public_id=ledger_entry.public_id,
    )
