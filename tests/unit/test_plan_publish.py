"""Unit tests for catalog plan publish."""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from billing_platform.services.catalog import (
    PlanNotDraftError,
    create_plan,
    create_product,
    publish_plan,
)


@pytest_asyncio.fixture
async def session(db_session: AsyncSession) -> AsyncSession:
    return db_session


@pytest_asyncio.fixture
async def published_plan(session: AsyncSession):
    product = await create_product(
        session,
        key="core_api",
        name="Core API",
    )
    plan = await create_plan(
        session,
        product_id=product.id,
        key="pro",
        billing_interval="month",
    )
    await publish_plan(session, plan.id)
    await session.commit()
    return plan


@pytest.mark.asyncio
async def test_cannot_publish_already_published(session, published_plan) -> None:
    with pytest.raises(PlanNotDraftError):
        await publish_plan(session, published_plan.id)


@pytest.mark.asyncio
async def test_publish_draft_sets_published_at(session) -> None:
    product = await create_product(session, key="snap_prod", name="Snap Product")
    plan = await create_plan(
        session,
        product_id=product.id,
        key="starter",
        billing_interval="month",
    )
    assert plan.published_at is None

    published = await publish_plan(session, plan.id)
    assert published.published_at is not None
    assert published.id == plan.id
