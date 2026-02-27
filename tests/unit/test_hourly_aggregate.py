"""Unit tests for hourly usage aggregation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from billing_platform.domain.ids import generate_uuidv7
from billing_platform.domain.models.organization import Organization
from billing_platform.domain.models.usage_aggregate import UsageAggregate
from billing_platform.domain.models.usage_event import UsageEvent
from billing_platform.services.usage import (
    UsageEventIn,
    aggregate_hour,
    ingest_usage_batch,
    list_usage_aggregates_for_period,
)
from billing_platform.workers.tasks.create_usage_partition import ensure_usage_partition

HOUR = datetime(2026, 2, 18, 10, 0, tzinfo=UTC)


@pytest_asyncio.fixture
async def session(db_session: AsyncSession) -> AsyncSession:
    return db_session


@pytest_asyncio.fixture
async def org_id(session: AsyncSession) -> int:
    organization = Organization(name="Hourly aggregate test organization")
    session.add(organization)
    await session.flush()
    return organization.id


async def count_aggregates(session: AsyncSession) -> int:
    count = await session.scalar(select(func.count()).select_from(UsageAggregate))
    assert count is not None
    return count


@pytest_asyncio.fixture
async def seeded_events(session: AsyncSession, org_id: int) -> int:
    """Seed usage events totaling 42 in HOUR bucket; returns organization_id."""
    await ensure_usage_partition(session, year=HOUR.year, month=HOUR.month)
    quantities = [10, 15, 17]
    for index, quantity in enumerate(quantities):
        session.add(
            UsageEvent(
                public_id=generate_uuidv7(),
                organization_id=org_id,
                feature_key="api_calls",
                quantity=Decimal(quantity),
                recorded_at=HOUR + timedelta(minutes=index * 10),
                idempotency_key=f"agg-seed-{index}",
            )
        )
    await session.flush()
    return org_id


@pytest.mark.asyncio
async def test_aggregate_hour_idempotent(session: AsyncSession, seeded_events: int) -> None:
    a1 = await aggregate_hour(
        session,
        organization_id=seeded_events,
        feature_key="api_calls",
        hour_start=HOUR,
    )
    a2 = await aggregate_hour(
        session,
        organization_id=seeded_events,
        feature_key="api_calls",
        hour_start=HOUR,
    )
    assert a1.quantity == a2.quantity == Decimal(42)
    assert await count_aggregates(session) == 1


@pytest.mark.asyncio
async def test_aggregate_hour_sums_only_events_in_bucket(
    session: AsyncSession, org_id: int
) -> None:
    await ensure_usage_partition(session, year=HOUR.year, month=HOUR.month)
    await ingest_usage_batch(
        session,
        organization_id=org_id,
        events=[
            UsageEventIn(
                feature_key="api_calls",
                quantity=5,
                idempotency_key="in-hour",
                recorded_at=HOUR + timedelta(minutes=30),
            ),
            UsageEventIn(
                feature_key="api_calls",
                quantity=99,
                idempotency_key="next-hour",
                recorded_at=HOUR + timedelta(hours=1),
            ),
        ],
    )
    result = await aggregate_hour(
        session,
        organization_id=org_id,
        feature_key="api_calls",
        hour_start=HOUR,
    )
    assert result.quantity == Decimal(5)
    assert await count_aggregates(session) == 1


@pytest.mark.asyncio
async def test_list_aggregates_includes_hour_containing_mid_hour_period_start(
    session: AsyncSession, org_id: int
) -> None:
    """GET /usage must not drop the hour bucket when current_period_start is mid-hour."""
    await ensure_usage_partition(session, year=HOUR.year, month=HOUR.month)
    await ingest_usage_batch(
        session,
        organization_id=org_id,
        events=[
            UsageEventIn(
                feature_key="api_calls",
                quantity=3,
                idempotency_key="mid-hour-period",
                recorded_at=HOUR + timedelta(minutes=20),
            ),
        ],
    )
    await aggregate_hour(
        session,
        organization_id=org_id,
        feature_key="api_calls",
        hour_start=HOUR,
    )
    rows = await list_usage_aggregates_for_period(
        session,
        organization_id=org_id,
        period_start=HOUR + timedelta(minutes=10),
        period_end=HOUR + timedelta(days=30),
    )
    assert len(rows) == 1
    assert rows[0].feature_key == "api_calls"
    assert rows[0].quantity == Decimal(3)
