from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from billing_platform.domain.models.feature import Feature
from billing_platform.domain.models.plan import Plan
from billing_platform.domain.models.plan_feature import PlanFeature
from billing_platform.domain.models.price import Price
from billing_platform.domain.models.product import Product


class CatalogError(Exception):
    """Base catalog service error."""


ALLOWED_FEATURE_TYPES = frozenset({"boolean", "quota", "rate_limit", "seat"})


class InvalidFeatureTypeError(CatalogError):
    """Feature type is not a supported entitlement feature_type."""


class PlanNotFoundError(CatalogError):
    """Plan id does not exist."""


class PlanNotDraftError(CatalogError):
    """Plan is already published or otherwise not a draft."""


class PlanDraftExistsError(CatalogError):
    """A draft plan already exists for this product/key."""


class ProductNotFoundError(CatalogError):
    """Product id does not exist."""


class FeatureNotFoundError(CatalogError):
    """Feature id does not exist."""


@dataclass(frozen=True)
class PlanFeatureInput:
    """Input row for PUT /plans/{id}/features."""

    feature_id: uuid.UUID
    limit_value: int | None = None
    is_enabled: bool = True
    enforcement_mode: str = "hard"


@dataclass(frozen=True)
class CatalogSnapshot:
    """Published catalog snapshot for cache warming."""

    products: list[Product]
    plans: list[Plan]
    prices: list[Price]
    features: list[Feature]
    plan_features: list[PlanFeature]


async def create_product(
    session: AsyncSession,
    *,
    key: str,
    name: str,
    description: str | None = None,
    is_active: bool = True,
) -> Product:
    product = Product(
        key=key,
        name=name,
        description=description,
        is_active=is_active,
    )
    session.add(product)
    await session.flush()
    return product


async def _get_product_or_raise(session: AsyncSession, product_id: uuid.UUID) -> Product:
    result = await session.execute(select(Product).where(Product.id == product_id))
    product = result.scalar_one_or_none()
    if product is None:
        raise ProductNotFoundError(f"product {product_id} not found")
    return product


async def _get_plan_or_raise(session: AsyncSession, plan_id: uuid.UUID) -> Plan:
    result = await session.execute(select(Plan).where(Plan.id == plan_id))
    plan = result.scalar_one_or_none()
    if plan is None:
        raise PlanNotFoundError(f"plan {plan_id} not found")
    return plan


async def _assert_plan_is_draft(plan: Plan) -> None:
    if plan.published_at is not None:
        raise PlanNotDraftError(f"plan {plan.id} is not a draft")


async def create_plan(
    session: AsyncSession,
    *,
    product_id: uuid.UUID,
    key: str,
    billing_interval: str,
    trial_days: int | None = None,
    grace_period_days: int = 7,
    dunning_policy: dict[str, object] | None = None,
    entitlement_policy: dict[str, object] | None = None,
) -> Plan:
    await _get_product_or_raise(session, product_id)

    draft_result = await session.execute(
        select(Plan).where(
            Plan.product_id == product_id,
            Plan.key == key,
            Plan.published_at.is_(None),
        )
    )
    if draft_result.scalar_one_or_none() is not None:
        raise PlanDraftExistsError(f"draft plan already exists for product {product_id} key {key}")

    version_result = await session.execute(
        select(func.coalesce(func.max(Plan.version), 0)).where(
            Plan.product_id == product_id,
            Plan.key == key,
        )
    )
    next_version = int(version_result.scalar_one()) + 1

    plan = Plan(
        product_id=product_id,
        key=key,
        billing_interval=billing_interval,
        trial_days=trial_days,
        grace_period_days=grace_period_days,
        dunning_policy=dunning_policy or {},
        entitlement_policy=entitlement_policy or {},
        version=next_version,
    )
    session.add(plan)
    await session.flush()
    return plan


async def create_price(
    session: AsyncSession,
    *,
    plan_id: uuid.UUID,
    unit_amount_cents: int,
    currency: str = "USD",
    pricing_model: str = "flat",
    metered_feature_key: str | None = None,
    external_price_id: str | None = None,
    is_active: bool = True,
) -> Price:
    plan = await _get_plan_or_raise(session, plan_id)
    await _assert_plan_is_draft(plan)

    price = Price(
        plan_id=plan_id,
        currency=currency,
        unit_amount_cents=unit_amount_cents,
        pricing_model=pricing_model,
        metered_feature_key=metered_feature_key,
        external_price_id=external_price_id,
        is_active=is_active,
    )
    session.add(price)
    await session.flush()
    return price


async def create_feature(
    session: AsyncSession,
    *,
    key: str,
    feature_type: str,
    default_limit: int | None = None,
    reset_interval: str | None = None,
) -> Feature:
    if feature_type not in ALLOWED_FEATURE_TYPES:
        raise InvalidFeatureTypeError(
            f"feature_type must be one of {sorted(ALLOWED_FEATURE_TYPES)}"
        )
    feature = Feature(
        key=key,
        feature_type=feature_type,
        default_limit=default_limit,
        reset_interval=reset_interval,
    )
    session.add(feature)
    await session.flush()
    return feature


async def set_plan_features(
    session: AsyncSession,
    plan_id: uuid.UUID,
    features: list[PlanFeatureInput],
) -> list[PlanFeature]:
    plan = await _get_plan_or_raise(session, plan_id)
    await _assert_plan_is_draft(plan)

    existing_result = await session.execute(
        select(PlanFeature).where(PlanFeature.plan_id == plan_id)
    )
    existing = {row.feature_id: row for row in existing_result.scalars().all()}

    updated: list[PlanFeature] = []
    for item in features:
        result = await session.execute(select(Feature).where(Feature.id == item.feature_id))
        if result.scalar_one_or_none() is None:
            raise FeatureNotFoundError(f"feature {item.feature_id} not found")

        row = existing.get(item.feature_id)
        if row is None:
            row = PlanFeature(
                plan_id=plan_id,
                feature_id=item.feature_id,
            )
            session.add(row)
        row.limit_value = item.limit_value
        row.is_enabled = item.is_enabled
        row.enforcement_mode = item.enforcement_mode
        updated.append(row)

    await session.flush()
    return updated


async def publish_plan(session: AsyncSession, plan_id: uuid.UUID) -> Plan:
    plan = await _get_plan_or_raise(session, plan_id)
    if plan.published_at is not None:
        raise PlanNotDraftError(f"plan {plan_id} is already published")

    plan.published_at = datetime.now(UTC)
    await session.flush()
    return plan


async def get_catalog_snapshot(session: AsyncSession) -> CatalogSnapshot:
    plans_result = await session.execute(
        select(Plan).where(Plan.published_at.is_not(None)).order_by(Plan.published_at)
    )
    plans = list(plans_result.scalars().all())
    plan_ids = [plan.id for plan in plans]

    products: list[Product] = []
    if plans:
        product_ids = {plan.product_id for plan in plans}
        products_result = await session.execute(
            select(Product).where(Product.id.in_(product_ids)).order_by(Product.key)
        )
        products = list(products_result.scalars().all())

    prices: list[Price] = []
    plan_features: list[PlanFeature] = []
    if plan_ids:
        prices_result = await session.execute(
            select(Price).where(Price.plan_id.in_(plan_ids)).order_by(Price.plan_id)
        )
        prices = list(prices_result.scalars().all())

        pf_result = await session.execute(
            select(PlanFeature)
            .where(PlanFeature.plan_id.in_(plan_ids))
            .order_by(PlanFeature.plan_id)
        )
        plan_features = list(pf_result.scalars().all())

    feature_ids = {pf.feature_id for pf in plan_features}
    features: list[Feature] = []
    if feature_ids:
        features_result = await session.execute(
            select(Feature).where(Feature.id.in_(feature_ids)).order_by(Feature.key)
        )
        features = list(features_result.scalars().all())

    return CatalogSnapshot(
        products=products,
        plans=plans,
        prices=prices,
        features=features,
        plan_features=plan_features,
    )
