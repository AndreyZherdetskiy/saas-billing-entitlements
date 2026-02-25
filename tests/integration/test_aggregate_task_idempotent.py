"""Integration tests for Celery hourly aggregate task idempotency."""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from billing_platform.config import get_settings
from billing_platform.db import close_db_engine
from billing_platform.domain.ids import generate_uuidv7
from billing_platform.domain.models.usage_aggregate import UsageAggregate
from billing_platform.domain.models.usage_event import UsageEvent
from billing_platform.workers.tasks.aggregate_usage_hourly import aggregate_hourly
from billing_platform.workers.tasks.create_usage_partition import ensure_usage_partition

HOUR = datetime(2026, 2, 18, 11, 0, tzinfo=UTC)


async def _seed_events(database_url: str) -> int:
    engine = create_async_engine(database_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with session_factory() as session:
            org_id = await session.scalar(
                text(
                    """
                    INSERT INTO organizations (public_id, name)
                    VALUES (:public_id, :name)
                    RETURNING id
                    """
                ),
                {"public_id": generate_uuidv7(), "name": "Celery aggregate test org"},
            )
            assert org_id is not None

            await ensure_usage_partition(session, year=HOUR.year, month=HOUR.month)
            for index, quantity in enumerate((7, 8, 9)):
                session.add(
                    UsageEvent(
                        public_id=generate_uuidv7(),
                        organization_id=org_id,
                        feature_key="api_calls",
                        quantity=Decimal(quantity),
                        recorded_at=HOUR + timedelta(minutes=index * 5),
                        idempotency_key=f"celery-agg-{index}",
                    )
                )
            await session.commit()
            return org_id
    finally:
        await engine.dispose()


async def _count_aggregate(database_url: str, org_id: int) -> tuple[int, Decimal | None]:
    engine = create_async_engine(database_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with session_factory() as session:
            count = await session.scalar(select(func.count()).select_from(UsageAggregate))
            quantity = await session.scalar(
                select(UsageAggregate.quantity).where(
                    UsageAggregate.organization_id == org_id,
                    UsageAggregate.feature_key == "api_calls",
                    UsageAggregate.hour_start == HOUR,
                )
            )
            assert count is not None
            return count, quantity
    finally:
        await engine.dispose()


@pytest.mark.integration
def test_aggregate_hourly_task_idempotent(migrated_postgres_url: str) -> None:
    org_id = asyncio.run(_seed_events(migrated_postgres_url))

    os.environ["DATABASE_URL"] = migrated_postgres_url
    get_settings.cache_clear()
    asyncio.run(close_db_engine())

    aggregate_hourly(
        organization_id=org_id,
        feature_key="api_calls",
        hour_start=HOUR.isoformat(),
    )
    aggregate_hourly(
        organization_id=org_id,
        feature_key="api_calls",
        hour_start=HOUR.isoformat(),
    )

    count, quantity = asyncio.run(_count_aggregate(migrated_postgres_url, org_id))
    assert count == 1
    assert quantity == Decimal(24)

    asyncio.run(close_db_engine())
    get_settings.cache_clear()
