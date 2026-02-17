"""Deterministic idempotent local demo catalog + tenant (fixed keys for Compose/.env.example)."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from billing_platform.domain.models.api_key import ApiKey, ApiKeyRole
from billing_platform.domain.models.feature import Feature
from billing_platform.domain.models.organization import Organization
from billing_platform.domain.models.plan import Plan
from billing_platform.domain.models.product import Product
from billing_platform.domain.models.subscription import Subscription
from billing_platform.services.api_keys import create_api_key, hash_api_key, verify_api_key
from billing_platform.services.catalog import (
    PlanFeatureInput,
    create_feature,
    create_plan,
    create_price,
    create_product,
    publish_plan,
    set_plan_features,
)
from billing_platform.services.organizations import create_organization
from billing_platform.services.subscriptions import create_subscription

PRODUCT_KEY = "core_api"
PLAN_KEY = "starter"
FEATURE_API_CALLS = "api_calls"
FEATURE_SEATS = "seats"
EXTERNAL_SUBSCRIPTION_ID = "sub_demo_seed_001"
ORG_IDEMPOTENCY_KEY = "seed-catalog-demo-org"
SUB_IDEMPOTENCY_KEY = "seed-catalog-demo-sub"

# Fixed local secrets — never use outside compose/dev (.env.example mirrors these).
DEMO_PLATFORM_ADMIN_RAW_KEY = "bp_local_demo_platform_admin_key_v1"
DEMO_ORG_PUBLIC_ID = UUID("01900000-0000-7000-8000-000000000001")


@dataclass(frozen=True, slots=True)
class DemoSeedResult:
    platform_admin_key: str
    organization_public_id: UUID
    subscription_public_id: UUID
    plan_id: UUID
    external_subscription_id: str
    feature_keys: tuple[str, ...]
    key_created: bool


async def _get_product_by_key(session: AsyncSession, key: str) -> Product | None:
    result = await session.execute(select(Product).where(Product.key == key))
    return result.scalar_one_or_none()


async def _get_feature_by_key(session: AsyncSession, key: str) -> Feature | None:
    result = await session.execute(select(Feature).where(Feature.key == key))
    return result.scalar_one_or_none()


async def _get_published_plan(
    session: AsyncSession,
    product_id: UUID,
    plan_key: str,
) -> Plan | None:
    result = await session.execute(
        select(Plan).where(
            Plan.product_id == product_id,
            Plan.key == plan_key,
            Plan.published_at.is_not(None),
        )
    )
    return result.scalar_one_or_none()


async def _ensure_catalog(session: AsyncSession) -> Plan:
    product = await _get_product_by_key(session, PRODUCT_KEY)
    if product is None:
        product = await create_product(
            session,
            key=PRODUCT_KEY,
            name="Core API",
            description="Demo product for local walkthrough",
        )

    api_calls = await _get_feature_by_key(session, FEATURE_API_CALLS)
    if api_calls is None:
        api_calls = await create_feature(
            session,
            key=FEATURE_API_CALLS,
            feature_type="quota",
            default_limit=1000,
            reset_interval="month",
        )

    seats = await _get_feature_by_key(session, FEATURE_SEATS)
    if seats is None:
        seats = await create_feature(
            session,
            key=FEATURE_SEATS,
            feature_type="seat",
            default_limit=5,
        )

    plan = await _get_published_plan(session, product.id, PLAN_KEY)
    if plan is not None:
        return plan

    plan = await create_plan(
        session,
        product_id=product.id,
        key=PLAN_KEY,
        billing_interval="month",
        trial_days=14,
        grace_period_days=7,
    )
    await set_plan_features(
        session,
        plan.id,
        [
            PlanFeatureInput(
                feature_id=api_calls.id,
                limit_value=5000,
                is_enabled=True,
                enforcement_mode="hard",
            ),
            PlanFeatureInput(
                feature_id=seats.id,
                limit_value=10,
                is_enabled=True,
                enforcement_mode="hard",
            ),
        ],
    )
    await create_price(
        session,
        plan_id=plan.id,
        unit_amount_cents=2900,
        currency="USD",
        pricing_model="flat",
    )
    return await publish_plan(session, plan.id)


async def _ensure_platform_admin_key(session: AsyncSession) -> tuple[str, bool]:
    digest = hash_api_key(DEMO_PLATFORM_ADMIN_RAW_KEY)
    result = await session.execute(
        select(ApiKey).where(
            ApiKey.key_hash == digest,
            ApiKey.revoked_at.is_(None),
        )
    )
    existing = result.scalar_one_or_none()
    if existing is not None and verify_api_key(DEMO_PLATFORM_ADMIN_RAW_KEY, existing.key_hash):
        return DEMO_PLATFORM_ADMIN_RAW_KEY, False

    await create_api_key(
        session,
        organization_id=None,
        role=ApiKeyRole.PLATFORM_ADMIN.value,
        raw=DEMO_PLATFORM_ADMIN_RAW_KEY,
    )
    return DEMO_PLATFORM_ADMIN_RAW_KEY, True


async def _ensure_demo_tenant(
    session: AsyncSession, plan: Plan
) -> tuple[Organization, Subscription]:
    org = await create_organization(
        session,
        name="Demo Organization",
        external_id="ext-demo-seed-001",
        idempotency_key=ORG_IDEMPOTENCY_KEY,
        billing_email="demo@example.com",
        public_id=DEMO_ORG_PUBLIC_ID,
    )
    subscription = await create_subscription(
        session,
        organization_id=org.id,
        plan_id=plan.id,
        idempotency_key=SUB_IDEMPOTENCY_KEY,
    )
    if subscription.external_subscription_id != EXTERNAL_SUBSCRIPTION_ID:
        subscription.external_subscription_id = EXTERNAL_SUBSCRIPTION_ID
        await session.flush()
    return org, subscription


async def ensure_demo_seed(session: AsyncSession) -> DemoSeedResult:
    plan = await _ensure_catalog(session)
    admin_key, key_created = await _ensure_platform_admin_key(session)
    org, subscription = await _ensure_demo_tenant(session, plan)
    return DemoSeedResult(
        platform_admin_key=admin_key,
        organization_public_id=org.public_id,
        subscription_public_id=subscription.public_id,
        plan_id=plan.id,
        external_subscription_id=EXTERNAL_SUBSCRIPTION_ID,
        feature_keys=(FEATURE_API_CALLS, FEATURE_SEATS),
        key_created=key_created,
    )
