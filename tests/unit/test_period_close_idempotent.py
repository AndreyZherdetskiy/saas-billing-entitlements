"""Unit tests: idempotent billing period close."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from billing_platform.domain.ids import generate_uuidv7
from billing_platform.domain.models.ledger import LedgerEntry
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

P0 = datetime(2026, 2, 1, tzinfo=UTC)
P1 = datetime(2026, 2, 28, 23, 59, 59, tzinfo=UTC)

ORG: int = 0


async def count_ledger(session: AsyncSession, *, entry_type: str) -> int:
    result = await session.execute(
        select(func.count()).select_from(LedgerEntry).where(LedgerEntry.entry_type == entry_type)
    )
    return int(result.scalar_one())


@pytest_asyncio.fixture
async def session(db_session: AsyncSession) -> AsyncSession:
    return db_session


@pytest_asyncio.fixture
async def org_with_aggregates(session: AsyncSession) -> int:
    """Org with metered plan, subscription, and hourly aggregates in [P0, P1)."""
    global ORG
    org = await create_organization(
        session,
        name="Period Close Org",
        external_id=f"ext-pc-{uuid.uuid4().hex[:8]}",
        idempotency_key=f"idem-pc-{uuid.uuid4().hex[:8]}",
    )
    ORG = org.id

    product = await create_product(
        session,
        key=f"pc_prod_{uuid.uuid4().hex[:6]}",
        name="Period Close Product",
    )
    plan = await create_plan(
        session,
        product_id=product.id,
        key=f"pc_plan_{uuid.uuid4().hex[:6]}",
        billing_interval="month",
        trial_days=0,
    )
    await create_price(
        session,
        plan_id=plan.id,
        unit_amount_cents=100,
        currency="USD",
        pricing_model="per_unit",
        metered_feature_key="api_calls",
    )
    await publish_plan(session, plan.id)

    subscription = Subscription(
        organization_id=org.id,
        plan_id=plan.id,
        status=SubscriptionStatus.active.value,
        current_period_start=P0,
        current_period_end=P1,
        external_subscription_id=f"sub_{uuid.uuid4().hex[:12]}",
    )
    session.add(subscription)
    await session.flush()

    hour = datetime(2026, 2, 18, 10, 0, tzinfo=UTC)
    session.add(
        UsageAggregate(
            public_id=generate_uuidv7(),
            organization_id=org.id,
            feature_key="api_calls",
            hour_start=hour,
            quantity=Decimal(42),
        )
    )
    await session.flush()
    return org.id


@pytest.mark.asyncio
async def test_period_close_idempotent(session: AsyncSession, org_with_aggregates: int) -> None:
    r1 = await close_billing_period(
        session,
        organization_id=ORG,
        period_start=P0,
        period_end=P1,
        idempotency_key="pc-1",
    )
    r2 = await close_billing_period(
        session,
        organization_id=ORG,
        period_start=P0,
        period_end=P1,
        idempotency_key="pc-1",
    )
    assert r1.invoice_public_id == r2.invoice_public_id
    assert await count_ledger(session, entry_type="usage_charge") == 1
