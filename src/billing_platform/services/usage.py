"""Tenant-scoped usage event ingestion."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from billing_platform.domain.ids import generate_uuidv7
from billing_platform.domain.models.usage_aggregate import UsageAggregate
from billing_platform.domain.models.usage_event import UsageEvent
from billing_platform.observability.metrics import increment_usage_events_ingested
from billing_platform.services.usage_partitions import ensure_usage_partition


@dataclass(frozen=True)
class UsageEventIn:
    feature_key: str
    quantity: int
    idempotency_key: str
    recorded_at: datetime | None = None


@dataclass(frozen=True)
class UsageBatchResult:
    accepted: int
    duplicates: int
    public_ids: list[str]


@dataclass(frozen=True)
class UsagePeriodAggregate:
    feature_key: str
    period_start: datetime
    period_end: datetime
    quantity: Decimal


def _normalize_hour_start(hour_start: datetime) -> datetime:
    if hour_start.tzinfo is None:
        raise ValueError("hour_start must be timezone-aware")
    normalized = hour_start.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    return normalized


async def aggregate_pending_hours(
    session: AsyncSession,
    *,
    lookback_hours: int = 48,
    now: datetime | None = None,
) -> int:
    """Aggregate all usage-event hour buckets in the lookback window."""
    reference = now or datetime.now(UTC)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=UTC)
    since = reference - timedelta(hours=lookback_hours)
    hour_bucket = func.date_trunc("hour", UsageEvent.recorded_at)

    rows = await session.execute(
        select(
            UsageEvent.organization_id,
            UsageEvent.feature_key,
            hour_bucket,
        )
        .where(
            UsageEvent.recorded_at >= since,
            UsageEvent.recorded_at < reference,
        )
        .group_by(UsageEvent.organization_id, UsageEvent.feature_key, hour_bucket)
    )

    processed = 0
    for organization_id, feature_key, bucket_start in rows.all():
        await aggregate_hour(
            session,
            organization_id=organization_id,
            feature_key=feature_key,
            hour_start=bucket_start,
        )
        processed += 1
    return processed


async def aggregate_hour(
    session: AsyncSession,
    *,
    organization_id: int,
    feature_key: str,
    hour_start: datetime,
) -> UsageAggregate:
    """UPSERT hourly quantity as SUM(events in [hour_start, hour_start+1h))."""
    bucket_start = _normalize_hour_start(hour_start)
    bucket_end = bucket_start + timedelta(hours=1)

    total = await session.scalar(
        select(func.coalesce(func.sum(UsageEvent.quantity), 0)).where(
            UsageEvent.organization_id == organization_id,
            UsageEvent.feature_key == feature_key,
            UsageEvent.recorded_at >= bucket_start,
            UsageEvent.recorded_at < bucket_end,
        )
    )
    quantity = Decimal(total if total is not None else 0)

    statement = (
        insert(UsageAggregate)
        .values(
            public_id=generate_uuidv7(),
            organization_id=organization_id,
            feature_key=feature_key,
            hour_start=bucket_start,
            quantity=quantity,
        )
        .on_conflict_do_update(
            index_elements=["organization_id", "feature_key", "hour_start"],
            set_={"quantity": quantity},
        )
        .returning(UsageAggregate)
    )
    result = await session.execute(statement)
    aggregate = result.scalar_one()
    await session.flush()
    return aggregate


async def list_usage_aggregates_for_period(
    session: AsyncSession,
    *,
    organization_id: int,
    period_start: datetime,
    period_end: datetime,
) -> list[UsagePeriodAggregate]:
    """Sum hourly usage aggregates per feature within a billing period."""
    if period_start.tzinfo is None or period_end.tzinfo is None:
        raise ValueError("period bounds must be timezone-aware")
    period_start = period_start.astimezone(UTC)
    period_end = period_end.astimezone(UTC)
    window_start = period_start.replace(minute=0, second=0, microsecond=0)

    result = await session.execute(
        select(
            UsageAggregate.feature_key,
            func.coalesce(func.sum(UsageAggregate.quantity), 0),
        )
        .where(
            UsageAggregate.organization_id == organization_id,
            UsageAggregate.hour_start >= window_start,
            UsageAggregate.hour_start < period_end,
        )
        .group_by(UsageAggregate.feature_key)
        .order_by(UsageAggregate.feature_key)
    )

    aggregates: list[UsagePeriodAggregate] = []
    for feature_key, quantity in result.all():
        qty = Decimal(quantity)
        if qty <= 0:
            continue
        aggregates.append(
            UsagePeriodAggregate(
                feature_key=feature_key,
                period_start=period_start,
                period_end=period_end,
                quantity=qty,
            )
        )
    return aggregates


async def ingest_usage_batch(
    session: AsyncSession,
    *,
    organization_id: int,
    events: list[UsageEventIn],
) -> UsageBatchResult:
    """Insert usage events while resolving idempotency keys across all partitions."""
    default_recorded_at = datetime.now(UTC)
    normalized_events: list[tuple[UsageEventIn, datetime]] = []

    for event in events:
        recorded_at = event.recorded_at or default_recorded_at
        if recorded_at.tzinfo is None:
            raise ValueError("recorded_at must be timezone-aware")
        recorded_at = recorded_at.astimezone(UTC)
        normalized_events.append((event, recorded_at))

    if not normalized_events:
        return UsageBatchResult(accepted=0, duplicates=0, public_ids=[])

    idempotency_keys = {event.idempotency_key for event, _ in normalized_events}
    existing_result = await session.execute(
        select(UsageEvent.public_id, UsageEvent.idempotency_key).where(
            UsageEvent.organization_id == organization_id,
            UsageEvent.idempotency_key.in_(idempotency_keys),
        )
    )
    public_ids_by_key = {
        idempotency_key: str(public_id) for public_id, idempotency_key in existing_result
    }

    rows: list[dict[str, object]] = []
    months: set[tuple[int, int]] = set()
    pending_keys: set[str] = set()
    for event, recorded_at in normalized_events:
        if event.idempotency_key in public_ids_by_key or event.idempotency_key in pending_keys:
            continue

        pending_keys.add(event.idempotency_key)
        months.add((recorded_at.year, recorded_at.month))
        rows.append(
            {
                "public_id": generate_uuidv7(),
                "organization_id": organization_id,
                "feature_key": event.feature_key,
                "quantity": Decimal(event.quantity),
                "recorded_at": recorded_at,
                "idempotency_key": event.idempotency_key,
            }
        )

    for year, month_number in sorted(months):
        await ensure_usage_partition(session, year=year, month=month_number)

    if not rows:
        return UsageBatchResult(
            accepted=0,
            duplicates=len(events),
            public_ids=[public_ids_by_key[event.idempotency_key] for event in events],
        )

    statement = (
        insert(UsageEvent).values(rows).on_conflict_do_nothing().returning(UsageEvent.public_id)
    )
    result = await session.execute(statement)
    accepted = len(result.scalars().all())

    public_id_rows = await session.execute(
        select(UsageEvent.public_id, UsageEvent.idempotency_key).where(
            UsageEvent.organization_id == organization_id,
            UsageEvent.idempotency_key.in_(idempotency_keys),
        )
    )
    public_ids_by_key = {
        idempotency_key: str(public_id) for public_id, idempotency_key in public_id_rows
    }
    public_ids = [public_ids_by_key[event.idempotency_key] for event in events]
    if accepted:
        increment_usage_events_ingested(accepted)
    return UsageBatchResult(
        accepted=accepted,
        duplicates=len(events) - accepted,
        public_ids=public_ids,
    )
