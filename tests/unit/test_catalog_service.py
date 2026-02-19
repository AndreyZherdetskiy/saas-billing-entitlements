"""Unit tests for catalog service."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from billing_platform.services.catalog import (
    FeatureNotFoundError,
    PlanDraftExistsError,
    PlanFeatureInput,
    PlanNotDraftError,
    ProductNotFoundError,
    create_feature,
    create_plan,
    create_price,
    create_product,
    get_catalog_snapshot,
    publish_plan,
    set_plan_features,
)


@pytest.mark.asyncio
async def test_create_plan_product_not_found(db_session: AsyncSession) -> None:
    with pytest.raises(ProductNotFoundError):
        await create_plan(
            db_session,
            product_id=uuid.uuid4(),
            key="missing",
            billing_interval="month",
        )


@pytest.mark.asyncio
async def test_create_plan_draft_exists(db_session: AsyncSession) -> None:
    product = await create_product(db_session, key=f"dup_prod_{uuid.uuid4().hex[:6]}", name="Dup")
    await create_plan(
        db_session,
        product_id=product.id,
        key="starter",
        billing_interval="month",
    )
    with pytest.raises(PlanDraftExistsError):
        await create_plan(
            db_session,
            product_id=product.id,
            key="starter",
            billing_interval="month",
        )


@pytest.mark.asyncio
async def test_create_price_and_feature_on_draft_plan(db_session: AsyncSession) -> None:
    product = await create_product(
        db_session,
        key=f"feat_prod_{uuid.uuid4().hex[:6]}",
        name="Feature Product",
        description="desc",
    )
    plan = await create_plan(
        db_session,
        product_id=product.id,
        key="pro",
        billing_interval="month",
    )
    price = await create_price(
        db_session,
        plan_id=plan.id,
        unit_amount_cents=9900,
        currency="USD",
    )
    feature = await create_feature(
        db_session,
        key=f"api_calls_{uuid.uuid4().hex[:6]}",
        feature_type="quota",
        default_limit=1000,
    )
    bindings = await set_plan_features(
        db_session,
        plan_id=plan.id,
        features=[
            PlanFeatureInput(
                feature_id=feature.id,
                limit_value=500,
                is_enabled=True,
                enforcement_mode="hard",
            )
        ],
    )

    assert price.unit_amount_cents == 9900
    assert len(bindings) == 1
    assert bindings[0].limit_value == 500


@pytest.mark.asyncio
async def test_create_price_rejects_published_plan(db_session: AsyncSession) -> None:
    product = await create_product(db_session, key=f"pub_prod_{uuid.uuid4().hex[:6]}", name="Pub")
    plan = await create_plan(
        db_session,
        product_id=product.id,
        key="live",
        billing_interval="month",
    )
    await publish_plan(db_session, plan.id)

    with pytest.raises(PlanNotDraftError):
        await create_price(db_session, plan_id=plan.id, unit_amount_cents=1000)


@pytest.mark.asyncio
async def test_set_plan_features_missing_feature(db_session: AsyncSession) -> None:
    product = await create_product(db_session, key=f"miss_feat_{uuid.uuid4().hex[:6]}", name="X")
    plan = await create_plan(
        db_session,
        product_id=product.id,
        key="basic",
        billing_interval="month",
    )
    with pytest.raises(FeatureNotFoundError):
        await set_plan_features(
            db_session,
            plan_id=plan.id,
            features=[PlanFeatureInput(feature_id=uuid.uuid4())],
        )


@pytest.mark.asyncio
async def test_get_catalog_snapshot_returns_published_rows(db_session: AsyncSession) -> None:
    product = await create_product(
        db_session,
        key=f"snap_prod_{uuid.uuid4().hex[:6]}",
        name="Snap",
    )
    plan = await create_plan(
        db_session,
        product_id=product.id,
        key="snapshot",
        billing_interval="month",
    )
    feature = await create_feature(
        db_session,
        key=f"snap_feat_{uuid.uuid4().hex[:6]}",
        feature_type="boolean",
    )
    await create_price(db_session, plan_id=plan.id, unit_amount_cents=500)
    await set_plan_features(
        db_session,
        plan_id=plan.id,
        features=[PlanFeatureInput(feature_id=feature.id, is_enabled=True)],
    )
    await publish_plan(db_session, plan.id)
    await db_session.commit()

    snapshot = await get_catalog_snapshot(db_session)
    assert len(snapshot.products) == 1
    assert len(snapshot.plans) == 1
    assert len(snapshot.prices) == 1
    assert len(snapshot.features) == 1
    assert len(snapshot.plan_features) == 1
