"""Integration: period close posts a single usage_charge ledger entry."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from billing_platform.domain.ids import generate_uuidv7
from billing_platform.domain.models.invoice import Invoice, InvoiceStatus
from billing_platform.domain.models.ledger import LedgerEntry, LedgerEntryType
from billing_platform.domain.models.subscription import Subscription, SubscriptionStatus
from billing_platform.domain.models.usage_aggregate import UsageAggregate
from billing_platform.services.catalog import (
    create_plan,
    create_price,
    create_product,
    publish_plan,
)
from billing_platform.services.organizations import create_organization
from billing_platform.services.period_close import close_billing_period


@pytest.mark.integration
async def test_period_close_posts_usage_charge_ledger(db_session: AsyncSession) -> None:
    period_start = datetime(2026, 2, 1, tzinfo=UTC)
    period_end = datetime(2026, 2, 28, 23, 59, 59, tzinfo=UTC)

    org = await create_organization(
        db_session,
        name="Period Close Ledger Org",
        external_id=f"ext-pcl-{uuid.uuid4().hex[:8]}",
        idempotency_key=f"idem-pcl-{uuid.uuid4().hex[:8]}",
    )
    product = await create_product(
        db_session,
        key=f"pcl_prod_{uuid.uuid4().hex[:6]}",
        name="PCL Product",
    )
    plan = await create_plan(
        db_session,
        product_id=product.id,
        key=f"pcl_plan_{uuid.uuid4().hex[:6]}",
        billing_interval="month",
        trial_days=0,
    )
    await create_price(
        db_session,
        plan_id=plan.id,
        unit_amount_cents=100,
        currency="USD",
        pricing_model="per_unit",
        metered_feature_key="api_calls",
    )
    await publish_plan(db_session, plan.id)

    subscription = Subscription(
        organization_id=org.id,
        plan_id=plan.id,
        status=SubscriptionStatus.active.value,
        current_period_start=period_start,
        current_period_end=period_end,
        external_subscription_id=f"sub_{uuid.uuid4().hex[:12]}",
    )
    db_session.add(subscription)
    await db_session.flush()

    db_session.add(
        UsageAggregate(
            public_id=generate_uuidv7(),
            organization_id=org.id,
            feature_key="api_calls",
            hour_start=datetime(2026, 2, 18, 10, 0, tzinfo=UTC),
            quantity=Decimal(10),
        )
    )
    await db_session.flush()

    result = await close_billing_period(
        db_session,
        organization_id=org.id,
        period_start=period_start,
        period_end=period_end,
        idempotency_key=f"pc-integ-{uuid.uuid4().hex[:8]}",
    )
    await db_session.commit()

    ledger_count = await db_session.scalar(
        select(func.count())
        .select_from(LedgerEntry)
        .where(
            LedgerEntry.organization_id == org.id,
            LedgerEntry.entry_type == LedgerEntryType.usage_charge.value,
        )
    )
    assert ledger_count == 1

    entry = await db_session.scalar(
        select(LedgerEntry).where(
            LedgerEntry.organization_id == org.id,
            LedgerEntry.entry_type == LedgerEntryType.usage_charge.value,
        )
    )
    assert entry is not None
    assert entry.amount_cents == 1000
    assert entry.invoice_id == result.invoice_id

    invoice = await db_session.get(Invoice, result.invoice_id)
    assert invoice is not None
    assert invoice.status == InvoiceStatus.open.value
    assert invoice.total_amount_cents == 1000
