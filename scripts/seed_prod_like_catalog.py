"""Catalog extension for prod-like seed (domain services only; does not call seed_catalog.py)."""

from __future__ import annotations

from dataclasses import dataclass

from seed_prod_like_profiles import ProdLikeProfile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from billing_platform.domain.models.feature import Feature
from billing_platform.domain.models.plan import Plan
from billing_platform.domain.models.product import Product
from billing_platform.services.catalog import (
    PlanFeatureInput,
    create_feature,
    create_plan,
    create_price,
    create_product,
    publish_plan,
    set_plan_features,
)

PRODUCT_KEY = "core_api"
FEATURE_API_CALLS = "api_calls"
FEATURE_SEATS = "seats"
FEATURE_SSO = "sso_enabled"
FEATURE_BURST = "burst_rpm"


@dataclass(frozen=True)
class CatalogContext:
    product: Product
    features: dict[str, Feature]
    plans: dict[str, Plan]


async def _get_product_by_key(session: AsyncSession, key: str) -> Product | None:
    result = await session.execute(select(Product).where(Product.key == key))
    return result.scalar_one_or_none()


async def _get_feature_by_key(session: AsyncSession, key: str) -> Feature | None:
    result = await session.execute(select(Feature).where(Feature.key == key))
    return result.scalar_one_or_none()


async def _get_plan_by_key(
    session: AsyncSession,
    product_id: object,
    plan_key: str,
) -> Plan | None:
    result = await session.execute(
        select(Plan)
        .where(Plan.product_id == product_id, Plan.key == plan_key)
        .order_by(Plan.version.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _ensure_feature(
    session: AsyncSession,
    *,
    key: str,
    feature_type: str,
    default_limit: int | None = None,
    reset_interval: str | None = None,
) -> Feature:
    existing = await _get_feature_by_key(session, key)
    if existing is not None:
        return existing
    return await create_feature(
        session,
        key=key,
        feature_type=feature_type,
        default_limit=default_limit,
        reset_interval=reset_interval,
    )


async def _ensure_published_plan(
    session: AsyncSession,
    product: Product,
    features: dict[str, Feature],
    *,
    plan_key: str,
    trial_days: int,
    api_limit: int,
    seat_limit: int,
    flat_cents: int,
    metered: bool = False,
) -> Plan:
    existing = await _get_plan_by_key(session, product.id, plan_key)
    if existing is not None and existing.published_at is not None:
        return existing

    if existing is not None and existing.published_at is None:
        plan = existing
    else:
        plan = await create_plan(
            session,
            product_id=product.id,
            key=plan_key,
            billing_interval="month",
            trial_days=trial_days,
            grace_period_days=7,
        )

    await set_plan_features(
        session,
        plan.id,
        [
            PlanFeatureInput(
                feature_id=features[FEATURE_API_CALLS].id,
                limit_value=api_limit,
                is_enabled=True,
                enforcement_mode="hard",
            ),
            PlanFeatureInput(
                feature_id=features[FEATURE_SEATS].id,
                limit_value=seat_limit,
                is_enabled=True,
                enforcement_mode="hard",
            ),
            PlanFeatureInput(
                feature_id=features[FEATURE_SSO].id,
                limit_value=None,
                is_enabled=plan_key != "starter",
                enforcement_mode="hard",
            ),
            PlanFeatureInput(
                feature_id=features[FEATURE_BURST].id,
                limit_value=100 if plan_key == "starter" else 500,
                is_enabled=True,
                enforcement_mode="hard",
            ),
        ],
    )

    # Only add prices if plan is still draft
    if plan.published_at is None:
        await create_price(
            session,
            plan_id=plan.id,
            unit_amount_cents=flat_cents,
            currency="USD",
            pricing_model="flat",
        )
        if metered:
            await create_price(
                session,
                plan_id=plan.id,
                unit_amount_cents=10,
                currency="USD",
                pricing_model="per_unit",
                metered_feature_key=FEATURE_API_CALLS,
            )
        plan = await publish_plan(session, plan.id)
    return plan


async def _ensure_draft_plan(
    session: AsyncSession,
    product: Product,
    features: dict[str, Feature],
) -> Plan:
    existing = await _get_plan_by_key(session, product.id, "enterprise_draft")
    if existing is not None:
        return existing

    plan = await create_plan(
        session,
        product_id=product.id,
        key="enterprise_draft",
        billing_interval="month",
        trial_days=0,
        grace_period_days=7,
    )
    await set_plan_features(
        session,
        plan.id,
        [
            PlanFeatureInput(
                feature_id=features[FEATURE_API_CALLS].id,
                limit_value=100_000,
                is_enabled=True,
                enforcement_mode="hard",
            ),
        ],
    )
    await create_price(
        session,
        plan_id=plan.id,
        unit_amount_cents=99_900,
        currency="USD",
        pricing_model="flat",
    )
    return plan


async def ensure_prod_like_catalog(
    session: AsyncSession,
    profile: ProdLikeProfile,
) -> CatalogContext:
    """Ensure catalog baseline + extensions; idempotent on global keys."""
    product = await _get_product_by_key(session, PRODUCT_KEY)
    if product is None:
        product = await create_product(
            session,
            key=PRODUCT_KEY,
            name="Core API",
            description="Prod-like catalog product",
        )

    features = {
        FEATURE_API_CALLS: await _ensure_feature(
            session,
            key=FEATURE_API_CALLS,
            feature_type="quota",
            default_limit=1000,
            reset_interval="month",
        ),
        FEATURE_SEATS: await _ensure_feature(
            session,
            key=FEATURE_SEATS,
            feature_type="seat",
            default_limit=5,
        ),
        FEATURE_SSO: await _ensure_feature(
            session,
            key=FEATURE_SSO,
            feature_type="boolean",
        ),
        FEATURE_BURST: await _ensure_feature(
            session,
            key=FEATURE_BURST,
            feature_type="rate_limit",
            default_limit=100,
            reset_interval="hour",
        ),
    }

    plans: dict[str, Plan] = {}
    plans["starter"] = await _ensure_published_plan(
        session,
        product,
        features,
        plan_key="starter",
        trial_days=14,
        api_limit=5_000,
        seat_limit=10,
        flat_cents=2900,
        metered=True,
    )
    plans["pro"] = await _ensure_published_plan(
        session,
        product,
        features,
        plan_key="pro",
        trial_days=14,
        api_limit=50_000,
        seat_limit=50,
        flat_cents=9900,
        metered=True,
    )
    if profile.plans_published_min >= 3:
        plans["enterprise"] = await _ensure_published_plan(
            session,
            product,
            features,
            plan_key="enterprise",
            trial_days=0,
            api_limit=500_000,
            seat_limit=500,
            flat_cents=49_900,
            metered=True,
        )
    if profile.include_archived_draft:
        plans["enterprise_draft"] = await _ensure_draft_plan(session, product, features)

    return CatalogContext(product=product, features=features, plans=plans)
