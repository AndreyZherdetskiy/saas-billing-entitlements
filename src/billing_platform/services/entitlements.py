"""Entitlement evaluator — read-only hot path (ADR-003)."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from billing_platform.config import get_settings
from billing_platform.domain.models.feature import Feature
from billing_platform.domain.models.plan import Plan
from billing_platform.domain.models.plan_feature import PlanFeature
from billing_platform.domain.models.subscription import Subscription, SubscriptionStatus
from billing_platform.domain.models.usage_aggregate import UsageAggregate
from billing_platform.integrations.redis_cache import (
    get_or_build_cached_snapshot,
    get_redis_client,
    increment_entitlement_version,
)
from billing_platform.observability.metrics import (
    increment_entitlement_evaluate,
    record_entitlement_evaluate_duration_seconds,
)
from billing_platform.services.grace import is_grace_active
from billing_platform.services.hotpath_cache import get_l1_snapshot, set_l1_snapshot
from billing_platform.services.subscriptions import get_primary_subscription


class EntitlementError(Exception):
    """Base entitlement service error."""


class OrganizationNotFoundError(EntitlementError):
    """Organization public_id does not exist."""


class SubscriptionNotFoundError(EntitlementError):
    """No subscription found for organization."""


@dataclass(frozen=True, slots=True)
class Check:
    """Single feature check in an evaluate request."""

    feature_key: str
    quantity: int = 1


@dataclass(frozen=True, slots=True)
class FeatureDecision:
    """Pure decision for a feature limit check."""

    allowed: bool
    limit: int | None
    used: int | None
    remaining: int | None
    reason: str | None


@dataclass(frozen=True, slots=True)
class AccessDecision:
    """Subscription-status access policy outcome."""

    allowed: bool
    mode: str


@dataclass(frozen=True, slots=True)
class EvaluateResult:
    """Per-feature evaluate outcome."""

    feature_key: str
    feature_type: str
    allowed: bool
    limit: int | None
    used: int | None
    remaining: int | None
    reason: str | None


@dataclass(frozen=True, slots=True)
class EvaluateResponse:
    """Full evaluate response including cache metadata."""

    organization_public_id: str
    subscription_status: str
    results: list[EvaluateResult]
    cache_hit: bool
    evaluated_at: datetime
    version: int


def _remaining(limit: int | None, used: int) -> int | None:
    if limit is None:
        return None
    return max(0, limit - used)


def _subscription_seat_quantity(subscription: Subscription) -> int | None:
    """Seat capacity from subscription metadata until subscription_items exists."""
    raw = subscription.metadata_.get("seat_quantity")
    if raw is None:
        return None
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        quantity = raw
    elif isinstance(raw, str):
        try:
            quantity = int(raw)
        except ValueError:
            return None
    else:
        return None
    return quantity if quantity >= 0 else None


def _align_hour_start(dt: datetime) -> datetime:
    return dt.replace(minute=0, second=0, microsecond=0)


def _rate_window_start(
    reset_interval: str | None,
    *,
    now: datetime,
    period_start: datetime,
) -> datetime:
    if reset_interval == "hour":
        return now.replace(minute=0, second=0, microsecond=0)
    if reset_interval == "day":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if reset_interval == "month":
        return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return period_start


def _effective_limit(
    *,
    feature_type: str,
    limit: int | None,
    seats: int | None,
) -> int | None:
    if feature_type == "seat" and seats is not None:
        return seats
    return limit


def _decide_numeric_feature(
    *,
    feature_type: str,
    limit: int | None,
    used: int,
    enforcement: str,
    quantity: int,
    seats: int | None,
) -> FeatureDecision:
    effective_limit = _effective_limit(feature_type=feature_type, limit=limit, seats=seats)
    remaining = _remaining(effective_limit, used)
    if feature_type == "rate_limit":
        exhausted_reason = "rate_limit_exhausted"
        exceeded_soft_reason = "rate_limit_exceeded_soft"
    elif feature_type == "seat":
        exhausted_reason = "seat_exhausted"
        exceeded_soft_reason = "seat_exceeded_soft"
    else:
        exhausted_reason = "quota_exhausted"
        exceeded_soft_reason = "quota_exceeded_soft"

    if effective_limit is not None and used >= effective_limit:
        if enforcement == "hard":
            return FeatureDecision(
                allowed=False,
                limit=effective_limit,
                used=used,
                remaining=0,
                reason=exhausted_reason,
            )
        return FeatureDecision(
            allowed=True,
            limit=effective_limit,
            used=used,
            remaining=0,
            reason=exceeded_soft_reason,
        )
    if effective_limit is not None and used + quantity > effective_limit and enforcement == "hard":
        return FeatureDecision(
            allowed=False,
            limit=effective_limit,
            used=used,
            remaining=remaining,
            reason=exhausted_reason,
        )
    return FeatureDecision(
        allowed=True,
        limit=effective_limit,
        used=used,
        remaining=remaining,
        reason=None,
    )


def decide_feature(
    *,
    feature_type: str,
    limit: int | None,
    used: int,
    enforcement: str,
    quantity: int = 1,
    is_enabled: bool = True,
    seats: int | None = None,
) -> FeatureDecision:
    """Decide whether a feature request is allowed given usage and enforcement."""
    if feature_type == "boolean":
        if not is_enabled:
            return FeatureDecision(
                allowed=False,
                limit=None,
                used=None,
                remaining=None,
                reason="feature_disabled",
            )
        return FeatureDecision(
            allowed=True,
            limit=None,
            used=None,
            remaining=None,
            reason=None,
        )

    if not is_enabled:
        return FeatureDecision(
            allowed=False,
            limit=limit,
            used=used,
            remaining=_remaining(limit, used),
            reason="feature_disabled",
        )

    if feature_type in ("quota", "rate_limit", "seat"):
        return _decide_numeric_feature(
            feature_type=feature_type,
            limit=limit,
            used=used,
            enforcement=enforcement,
            quantity=quantity,
            seats=seats,
        )

    return FeatureDecision(
        allowed=False,
        limit=limit,
        used=used,
        remaining=_remaining(limit, used),
        reason="feature_misconfigured",
    )


def decide_access(
    *,
    status: str,
    grace_active: bool,
    enforcement: str,
) -> AccessDecision:
    """Apply subscription-status policy (active / past_due+grace / revoke)."""
    if status in (SubscriptionStatus.active.value, SubscriptionStatus.trialing.value):
        return AccessDecision(allowed=True, mode="full")

    if status == SubscriptionStatus.past_due.value:
        if grace_active:
            if enforcement == "degraded":
                return AccessDecision(allowed=True, mode="degraded")
            return AccessDecision(allowed=True, mode="full")
        return AccessDecision(allowed=False, mode="denied")

    return AccessDecision(allowed=False, mode="denied")


async def bump_entitlement_version(redis: Redis, *, organization_id: int) -> int:
    """INCR version key and DELETE the org snapshot key."""
    return await increment_entitlement_version(redis, organization_id=organization_id)


async def _load_subscription(
    session: AsyncSession,
    organization_id: int,
) -> tuple[Subscription, Plan]:
    subscription = await get_primary_subscription(session, organization_id)
    if subscription is None:
        raise SubscriptionNotFoundError(f"no subscription for organization {organization_id}")
    plan_result = await session.execute(select(Plan).where(Plan.id == subscription.plan_id))
    plan = plan_result.scalar_one()
    return subscription, plan


async def _sum_usage_for_window(
    session: AsyncSession,
    *,
    organization_id: int,
    feature_key: str,
    window_start: datetime,
    window_end: datetime,
) -> int:
    result = await session.execute(
        select(func.coalesce(func.sum(UsageAggregate.quantity), 0)).where(
            UsageAggregate.organization_id == organization_id,
            UsageAggregate.feature_key == feature_key,
            UsageAggregate.hour_start >= window_start,
            UsageAggregate.hour_start < window_end,
        )
    )
    total = result.scalar_one()
    return int(Decimal(total))


async def _load_feature_usage(
    session: AsyncSession,
    *,
    organization_id: int,
    subscription: Subscription,
    features: dict[str, dict[str, Any]],
) -> dict[str, int]:
    now = datetime.now(UTC)
    period_start = subscription.current_period_start.astimezone(UTC)
    period_end = subscription.current_period_end.astimezone(UTC)
    usage: dict[str, int] = {}
    for feature_key, feature_data in features.items():
        feature_type = str(feature_data.get("feature_type", "boolean"))
        if feature_type == "boolean":
            continue
        if feature_type == "rate_limit":
            window_start = _rate_window_start(
                feature_data.get("reset_interval"),
                now=now,
                period_start=period_start,
            )
            window_start = max(_align_hour_start(period_start), window_start)
        else:
            window_start = _align_hour_start(period_start)
        usage[feature_key] = await _sum_usage_for_window(
            session,
            organization_id=organization_id,
            feature_key=feature_key,
            window_start=window_start,
            window_end=period_end,
        )
    return usage


async def _load_plan_features(
    session: AsyncSession,
    plan_id: UUID,
) -> dict[str, dict[str, Any]]:
    result = await session.execute(
        select(PlanFeature, Feature)
        .join(Feature, PlanFeature.feature_id == Feature.id)
        .where(PlanFeature.plan_id == plan_id)
    )
    features: dict[str, dict[str, Any]] = {}
    for plan_feature, feature in result.all():
        features[feature.key] = {
            "feature_type": feature.feature_type,
            "reset_interval": feature.reset_interval,
            "limit": plan_feature.limit_value,
            "used": 0,
            "seats": None,
            "is_enabled": plan_feature.is_enabled,
            "enforcement_mode": plan_feature.enforcement_mode,
        }
    return features


async def _build_snapshot(
    session: AsyncSession,
    *,
    organization_id: int,
) -> dict[str, Any]:
    subscription, plan = await _load_subscription(session, organization_id)
    now = datetime.now(UTC)
    grace_active = is_grace_active(
        status=subscription.status,
        grace_period_days=plan.grace_period_days,
        past_due_entered_at=subscription.past_due_entered_at,
        now=now,
    )
    features = await _load_plan_features(session, plan.id)
    seat_quantity = _subscription_seat_quantity(subscription)
    usage_by_key = await _load_feature_usage(
        session,
        organization_id=organization_id,
        subscription=subscription,
        features=features,
    )
    for feature_key, feature_data in features.items():
        feature_data["used"] = usage_by_key.get(feature_key, 0)
        if str(feature_data.get("feature_type")) == "seat":
            feature_data["seats"] = seat_quantity
    return {
        "subscription_status": subscription.status,
        "grace_active": grace_active,
        "features": features,
    }


def _evaluate_checks(
    snapshot: dict[str, Any],
    checks: list[Check],
) -> list[EvaluateResult]:
    subscription_status = str(snapshot["subscription_status"])
    grace_active = bool(snapshot.get("grace_active", False))
    feature_map: dict[str, dict[str, Any]] = snapshot.get("features", {})

    results: list[EvaluateResult] = []
    for check in checks:
        feature_data = feature_map.get(check.feature_key)
        if feature_data is None:
            results.append(
                EvaluateResult(
                    feature_key=check.feature_key,
                    feature_type="unknown",
                    allowed=False,
                    limit=None,
                    used=None,
                    remaining=None,
                    reason="feature_not_in_plan",
                )
            )
            continue

        feature_type = str(feature_data.get("feature_type", "boolean"))
        enforcement = str(feature_data.get("enforcement_mode", "hard"))
        access = decide_access(
            status=subscription_status,
            grace_active=grace_active,
            enforcement=enforcement,
        )
        if not access.allowed:
            used_val = int(feature_data.get("used", 0))
            limit_val = feature_data.get("limit")
            results.append(
                EvaluateResult(
                    feature_key=check.feature_key,
                    feature_type=feature_type,
                    allowed=False,
                    limit=limit_val,
                    used=feature_data.get("used"),
                    remaining=_remaining(limit_val, used_val),
                    reason="subscription_access_revoked",
                )
            )
            continue

        limit_raw = feature_data.get("limit")
        limit = int(limit_raw) if limit_raw is not None else None
        used = int(feature_data.get("used", 0))
        seats_raw = feature_data.get("seats")
        seats = int(seats_raw) if seats_raw is not None else None
        decision = decide_feature(
            feature_type=feature_type,
            limit=limit,
            used=used,
            enforcement=enforcement,
            quantity=check.quantity,
            is_enabled=bool(feature_data.get("is_enabled", True)),
            seats=seats,
        )
        results.append(
            EvaluateResult(
                feature_key=check.feature_key,
                feature_type=feature_type,
                allowed=decision.allowed,
                limit=decision.limit,
                used=decision.used,
                remaining=decision.remaining,
                reason=decision.reason,
            )
        )
    return results


async def evaluate(
    redis: Redis | None,
    *,
    organization_id: int,
    organization_public_id: UUID,
    checks: list[Check],
    session: AsyncSession | None = None,
    session_provider: Callable[[], AsyncIterator[AsyncSession]] | None = None,
) -> EvaluateResponse:
    """L1 then Redis. Build from Postgres only on miss (session required then)."""
    started = time.perf_counter()
    snapshot = get_l1_snapshot(organization_id)
    cache_hit = True
    if snapshot is None:
        if redis is None:
            redis = await get_redis_client()
        ttl = get_settings().entitlement_cache_ttl_seconds

        async def builder() -> dict[str, Any]:
            if session is not None:
                return await _build_snapshot(session, organization_id=organization_id)
            if session_provider is None:
                msg = "entitlement snapshot miss requires a database session"
                raise RuntimeError(msg)
            async for db in session_provider():
                return await _build_snapshot(db, organization_id=organization_id)
            msg = "session provider yielded no session"
            raise RuntimeError(msg)

        snapshot, cache_hit = await get_or_build_cached_snapshot(
            redis,
            organization_id=organization_id,
            ttl_seconds=ttl,
            builder=builder,
        )
        set_l1_snapshot(organization_id, snapshot)

    raw_version = snapshot.get("cache_version")
    version = raw_version if isinstance(raw_version, int) else 1

    evaluated_at = datetime.now(UTC)
    results = _evaluate_checks(snapshot, checks)

    increment_entitlement_evaluate(cache_hit=cache_hit)
    record_entitlement_evaluate_duration_seconds(time.perf_counter() - started)

    return EvaluateResponse(
        organization_public_id=str(organization_public_id),
        subscription_status=str(snapshot["subscription_status"]),
        results=results,
        cache_hit=cache_hit,
        evaluated_at=evaluated_at,
        version=version,
    )
