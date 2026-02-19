"""Catalog HTTP routes (API B)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from billing_platform.api.deps import get_auth_context, require_platform_admin
from billing_platform.api.openapi_docs import (
    AUTH_RESPONSES,
    BAD_REQUEST_RESPONSE,
    CONFLICT_RESPONSE,
    FORBIDDEN_RESPONSE,
    NOT_FOUND_RESPONSE,
    merge_responses,
)
from billing_platform.db import get_session
from billing_platform.domain.models.api_key import ApiKeyRole
from billing_platform.domain.models.feature import Feature
from billing_platform.domain.models.plan import Plan
from billing_platform.domain.models.plan_feature import PlanFeature
from billing_platform.domain.models.price import Price
from billing_platform.domain.models.product import Product
from billing_platform.services.api_keys import AuthContext
from billing_platform.services.catalog import (
    CatalogError,
    FeatureNotFoundError,
    PlanDraftExistsError,
    PlanFeatureInput,
    PlanNotDraftError,
    PlanNotFoundError,
    ProductNotFoundError,
    create_feature,
    create_plan,
    create_price,
    create_product,
    get_catalog_snapshot,
    publish_plan,
    set_plan_features,
)

router = APIRouter(tags=["catalog"])

_PLAN_ID = Path(description="External UUIDv7 of the plan (not internal BIGINT id).")


class ProductResponse(BaseModel):
    id: UUID = Field(description="External UUIDv7 of the product (not internal BIGINT id).")
    key: str = Field(description="Stable product key (e.g. core_api).")
    name: str = Field(description="Display name of the product.")
    description: str | None = Field(default=None, description="Optional product description.")
    is_active: bool = Field(description="Whether the product is available for new plans.")


class CreateProductRequest(BaseModel):
    key: str = Field(description="Stable product key; unique across products.")
    name: str = Field(description="Display name of the product.")
    description: str | None = Field(default=None, description="Optional product description.")
    is_active: bool = Field(default=True, description="Whether the product is active.")


class PlanResponse(BaseModel):
    id: UUID = Field(description="External UUIDv7 of the plan (not internal BIGINT id).")
    product_id: UUID = Field(description="External UUIDv7 of the parent product.")
    key: str = Field(description="Stable plan key within the product.")
    billing_interval: str = Field(description="Billing cadence (e.g. month, year).")
    trial_days: int | None = Field(default=None, description="Trial length in days, if any.")
    grace_period_days: int = Field(description="Grace period days before dunning starts.")
    dunning_policy: dict[str, object] = Field(
        description="JSON dunning policy configuration for failed payments.",
    )
    entitlement_policy: dict[str, object] = Field(
        description="JSON entitlement policy overrides for this plan.",
    )
    version: int = Field(description="Published plan version; increments on each publish.")
    published_at: datetime | None = Field(
        default=None,
        description="UTC timestamp when the plan was published; null for drafts.",
    )


class CreatePlanRequest(BaseModel):
    product_id: UUID = Field(description="External UUIDv7 of the parent product.")
    key: str = Field(description="Stable plan key within the product.")
    billing_interval: str = Field(description="Billing cadence (e.g. month, year).")
    trial_days: int | None = Field(default=None, description="Trial length in days, if any.")
    grace_period_days: int = Field(default=7, description="Grace period days before dunning.")
    dunning_policy: dict[str, object] = Field(
        default_factory=dict,
        description="JSON dunning policy configuration.",
    )
    entitlement_policy: dict[str, object] = Field(
        default_factory=dict,
        description="JSON entitlement policy overrides.",
    )


class PriceResponse(BaseModel):
    id: UUID = Field(description="External UUIDv7 of the price (not internal BIGINT id).")
    plan_id: UUID = Field(description="External UUIDv7 of the parent plan.")
    currency: str = Field(description="ISO 4217 currency code (e.g. USD).")
    unit_amount_cents: int = Field(description="Price per unit in minor currency units.")
    pricing_model: str = Field(description="Pricing model: flat or metered.")
    metered_feature_key: str | None = Field(
        default=None,
        description="Feature key for metered pricing, if applicable.",
    )
    external_price_id: str | None = Field(
        default=None,
        description="External provider price id (e.g. Stripe price id).",
    )
    is_active: bool = Field(description="Whether the price is available for billing.")


class CreatePriceRequest(BaseModel):
    plan_id: UUID = Field(description="External UUIDv7 of the parent plan.")
    unit_amount_cents: int = Field(description="Price per unit in minor currency units.")
    currency: str = Field(default="USD", description="ISO 4217 currency code.")
    pricing_model: str = Field(default="flat", description="Pricing model: flat or metered.")
    metered_feature_key: str | None = Field(
        default=None,
        description="Feature key for metered pricing, if applicable.",
    )
    external_price_id: str | None = Field(
        default=None,
        description="External provider price id (e.g. Stripe price id).",
    )
    is_active: bool = Field(default=True, description="Whether the price is active.")


class FeatureResponse(BaseModel):
    id: UUID = Field(description="External UUIDv7 of the feature (not internal BIGINT id).")
    key: str = Field(description="Stable feature key (e.g. api_calls, seats).")
    feature_type: str = Field(
        description="Feature type: boolean, quota, rate_limit, or seat.",
    )
    default_limit: int | None = Field(
        default=None,
        description="Default limit when not overridden on the plan.",
    )
    reset_interval: str | None = Field(
        default=None,
        description="Usage reset interval for quota features (e.g. month).",
    )


class CreateFeatureRequest(BaseModel):
    key: str = Field(description="Stable feature key; unique across features.")
    feature_type: str = Field(
        description="Feature type: boolean, quota, rate_limit, or seat.",
    )
    default_limit: int | None = Field(
        default=None,
        description="Default limit when not overridden on the plan.",
    )
    reset_interval: str | None = Field(
        default=None,
        description="Usage reset interval for quota features.",
    )


class PlanFeatureItemRequest(BaseModel):
    feature_id: UUID = Field(description="External UUIDv7 of the feature.")
    limit_value: int | None = Field(
        default=None,
        description="Plan-specific limit override for quota/seat features.",
    )
    is_enabled: bool = Field(
        default=True,
        description="Whether the feature is enabled on the plan.",
    )
    enforcement_mode: str = Field(
        default="hard",
        description="Enforcement mode: hard denies usage; soft allows with audit.",
    )


class SetPlanFeaturesRequest(BaseModel):
    features: list[PlanFeatureItemRequest] = Field(
        description="Full replacement list of plan feature entitlements.",
    )


class PlanFeatureResponse(BaseModel):
    id: UUID = Field(
        description="External UUIDv7 of the plan-feature link (not internal BIGINT id).",
    )
    plan_id: UUID = Field(description="External UUIDv7 of the plan.")
    feature_id: UUID = Field(description="External UUIDv7 of the feature.")
    limit_value: int | None = Field(
        default=None,
        description="Plan-specific limit override for quota/seat features.",
    )
    is_enabled: bool = Field(description="Whether the feature is enabled on the plan.")
    enforcement_mode: str = Field(description="Enforcement mode: hard or soft.")


class CatalogSnapshotResponse(BaseModel):
    products: list[ProductResponse] = Field(description="All catalog products.")
    plans: list[PlanResponse] = Field(description="All catalog plans.")
    prices: list[PriceResponse] = Field(description="All catalog prices.")
    features: list[FeatureResponse] = Field(description="All entitlement features.")
    plan_features: list[PlanFeatureResponse] = Field(
        description="All plan-to-feature entitlement links.",
    )


def _product_response(product: Product) -> ProductResponse:
    return ProductResponse(
        id=product.id,
        key=product.key,
        name=product.name,
        description=product.description,
        is_active=product.is_active,
    )


def _plan_response(plan: Plan) -> PlanResponse:
    return PlanResponse(
        id=plan.id,
        product_id=plan.product_id,
        key=plan.key,
        billing_interval=plan.billing_interval,
        trial_days=plan.trial_days,
        grace_period_days=plan.grace_period_days,
        dunning_policy=plan.dunning_policy,
        entitlement_policy=plan.entitlement_policy,
        version=plan.version,
        published_at=plan.published_at,
    )


def _price_response(price: Price) -> PriceResponse:
    return PriceResponse(
        id=price.id,
        plan_id=price.plan_id,
        currency=price.currency,
        unit_amount_cents=price.unit_amount_cents,
        pricing_model=price.pricing_model,
        metered_feature_key=price.metered_feature_key,
        external_price_id=price.external_price_id,
        is_active=price.is_active,
    )


def _feature_response(feature: Feature) -> FeatureResponse:
    return FeatureResponse(
        id=feature.id,
        key=feature.key,
        feature_type=feature.feature_type,
        default_limit=feature.default_limit,
        reset_interval=feature.reset_interval,
    )


def _plan_feature_response(row: PlanFeature) -> PlanFeatureResponse:
    return PlanFeatureResponse(
        id=row.id,
        plan_id=row.plan_id,
        feature_id=row.feature_id,
        limit_value=row.limit_value,
        is_enabled=row.is_enabled,
        enforcement_mode=row.enforcement_mode,
    )


def _catalog_error_to_http(exc: CatalogError) -> HTTPException:
    if isinstance(exc, ProductNotFoundError | PlanNotFoundError | FeatureNotFoundError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, PlanNotDraftError):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    if isinstance(exc, PlanDraftExistsError):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.post(
    "/products",
    response_model=ProductResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create product",
    description=(
        "Creates a catalog product that groups plans. Platform_admin only. "
        "Does not create plans or prices — use the plan and price endpoints next."
    ),
    responses=merge_responses(AUTH_RESPONSES, FORBIDDEN_RESPONSE),
)
async def post_product(
    body: CreateProductRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    _: Annotated[AuthContext, Depends(require_platform_admin)],
) -> ProductResponse:
    product = await create_product(
        session,
        key=body.key,
        name=body.name,
        description=body.description,
        is_active=body.is_active,
    )
    await session.commit()
    return _product_response(product)


@router.post(
    "/plans",
    response_model=PlanResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create plan draft",
    description=(
        "Creates an unpublished plan draft under a product with billing interval, trial days, "
        "grace period, and dunning/entitlement policies. Platform_admin only. "
        "Must be published before subscriptions can use it."
    ),
    responses=merge_responses(
        AUTH_RESPONSES,
        FORBIDDEN_RESPONSE,
        NOT_FOUND_RESPONSE,
        BAD_REQUEST_RESPONSE,
    ),
)
async def post_plan(
    body: CreatePlanRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    _: Annotated[AuthContext, Depends(require_platform_admin)],
) -> PlanResponse:
    try:
        plan = await create_plan(
            session,
            product_id=body.product_id,
            key=body.key,
            billing_interval=body.billing_interval,
            trial_days=body.trial_days,
            grace_period_days=body.grace_period_days,
            dunning_policy=body.dunning_policy,
            entitlement_policy=body.entitlement_policy,
        )
    except CatalogError as exc:
        raise _catalog_error_to_http(exc) from exc
    await session.commit()
    return _plan_response(plan)


@router.post(
    "/prices",
    response_model=PriceResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create price",
    description=(
        "Adds a price to a plan (flat or metered, optional external Stripe price ID). "
        "Platform_admin only."
    ),
    responses=merge_responses(
        AUTH_RESPONSES,
        FORBIDDEN_RESPONSE,
        NOT_FOUND_RESPONSE,
        BAD_REQUEST_RESPONSE,
    ),
)
async def post_price(
    body: CreatePriceRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    _: Annotated[AuthContext, Depends(require_platform_admin)],
) -> PriceResponse:
    try:
        price = await create_price(
            session,
            plan_id=body.plan_id,
            unit_amount_cents=body.unit_amount_cents,
            currency=body.currency,
            pricing_model=body.pricing_model,
            metered_feature_key=body.metered_feature_key,
            external_price_id=body.external_price_id,
            is_active=body.is_active,
        )
    except CatalogError as exc:
        raise _catalog_error_to_http(exc) from exc
    await session.commit()
    return _price_response(price)


@router.post(
    "/features",
    response_model=FeatureResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create feature",
    description=(
        "Defines an entitlement feature (boolean, quota, rate_limit, or seat) with optional "
        "default limits and reset interval. Platform_admin only."
    ),
    responses=merge_responses(
        AUTH_RESPONSES,
        FORBIDDEN_RESPONSE,
        BAD_REQUEST_RESPONSE,
    ),
)
async def post_feature(
    body: CreateFeatureRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    _: Annotated[AuthContext, Depends(require_platform_admin)],
) -> FeatureResponse:
    try:
        feature = await create_feature(
            session,
            key=body.key,
            feature_type=body.feature_type,
            default_limit=body.default_limit,
            reset_interval=body.reset_interval,
        )
    except CatalogError as exc:
        raise _catalog_error_to_http(exc) from exc
    await session.commit()
    return _feature_response(feature)


@router.put(
    "/plans/{plan_id}/features",
    response_model=list[PlanFeatureResponse],
    summary="Set plan features",
    description=(
        "Replaces the full feature entitlement set on a draft plan (limits, enabled flags, "
        "enforcement mode). Platform_admin only. Published plans cannot be modified."
    ),
    responses=merge_responses(
        AUTH_RESPONSES,
        FORBIDDEN_RESPONSE,
        NOT_FOUND_RESPONSE,
        BAD_REQUEST_RESPONSE,
        CONFLICT_RESPONSE,
    ),
)
async def put_plan_features(
    plan_id: Annotated[UUID, _PLAN_ID],
    body: SetPlanFeaturesRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    _: Annotated[AuthContext, Depends(require_platform_admin)],
) -> list[PlanFeatureResponse]:
    try:
        rows = await set_plan_features(
            session,
            plan_id,
            [
                PlanFeatureInput(
                    feature_id=item.feature_id,
                    limit_value=item.limit_value,
                    is_enabled=item.is_enabled,
                    enforcement_mode=item.enforcement_mode,
                )
                for item in body.features
            ],
        )
    except CatalogError as exc:
        raise _catalog_error_to_http(exc) from exc
    await session.commit()
    return [_plan_feature_response(row) for row in rows]


@router.post(
    "/plans/{plan_id}/publish",
    response_model=PlanResponse,
    summary="Publish plan",
    description=(
        "Publishes a draft plan so it becomes available for new subscriptions. "
        "Platform_admin only. Increments plan version; existing subscriptions keep their "
        "subscribed version."
    ),
    responses=merge_responses(
        AUTH_RESPONSES,
        FORBIDDEN_RESPONSE,
        NOT_FOUND_RESPONSE,
        BAD_REQUEST_RESPONSE,
        CONFLICT_RESPONSE,
    ),
)
async def post_publish_plan(
    plan_id: Annotated[UUID, _PLAN_ID],
    session: Annotated[AsyncSession, Depends(get_session)],
    _: Annotated[AuthContext, Depends(require_platform_admin)],
) -> PlanResponse:
    try:
        plan = await publish_plan(session, plan_id)
    except CatalogError as exc:
        raise _catalog_error_to_http(exc) from exc
    await session.commit()
    return _plan_response(plan)


@router.get(
    "/catalog/snapshot",
    response_model=CatalogSnapshotResponse,
    summary="Get catalog snapshot",
    description=(
        "Returns the full catalog in one response: products, plans, prices, features, and "
        "plan-feature links. Platform_admin only. Useful for bulk export or admin dashboards."
    ),
    responses=merge_responses(AUTH_RESPONSES, FORBIDDEN_RESPONSE),
)
async def get_snapshot(
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> CatalogSnapshotResponse:
    if ctx.role != ApiKeyRole.PLATFORM_ADMIN.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="only platform_admin may read catalog snapshot",
        )
    snapshot = await get_catalog_snapshot(session)
    return CatalogSnapshotResponse(
        products=[_product_response(p) for p in snapshot.products],
        plans=[_plan_response(p) for p in snapshot.plans],
        prices=[_price_response(p) for p in snapshot.prices],
        features=[_feature_response(f) for f in snapshot.features],
        plan_features=[_plan_feature_response(pf) for pf in snapshot.plan_features],
    )
