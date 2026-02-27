"""Unit tests for subscription create idempotency."""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from billing_platform.services.catalog import create_plan, create_product, publish_plan
from billing_platform.services.organizations import create_organization
from billing_platform.services.subscriptions import create_subscription


@pytest_asyncio.fixture
async def session(db_session: AsyncSession) -> AsyncSession:
    return db_session


@pytest_asyncio.fixture
async def org_and_published_plan(session: AsyncSession):
    org = await create_organization(
        session,
        name="Sub Corp",
        external_id="ext-sub-corp",
        idempotency_key="idem-org-sub",
    )
    product = await create_product(session, key="api", name="API")
    plan = await create_plan(
        session,
        product_id=product.id,
        key="pro",
        billing_interval="month",
        trial_days=14,
    )
    await publish_plan(session, plan.id)
    await session.commit()
    return org, plan


@pytest.mark.asyncio
async def test_create_subscription_retry_same_idempotency_key(
    session: AsyncSession,
    org_and_published_plan,
) -> None:
    org, plan = org_and_published_plan
    first = await create_subscription(
        session,
        organization_id=org.id,
        plan_id=plan.id,
        idempotency_key="idem-sub-001",
    )
    second = await create_subscription(
        session,
        organization_id=org.id,
        plan_id=plan.id,
        idempotency_key="idem-sub-001",
    )
    assert second.public_id == first.public_id
    assert second.id == first.id
