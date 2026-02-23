import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from billing_platform.domain.ids import generate_uuidv7
from billing_platform.workers.tasks.create_usage_partition import (
    ensure_usage_partition,
    month_bounds,
)


@pytest.mark.integration
async def test_ensure_usage_partition_serializes_concurrent_creation(
    migrated_postgres_url: str,
) -> None:
    _, next_start = month_bounds(datetime.now(UTC))
    _, target_start = month_bounds(next_start)
    partition_name = f"usage_events_{target_start:%Y_%m}"
    engine = create_async_engine(migrated_postgres_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    barrier = asyncio.Barrier(2)

    async def create_partition() -> str:
        async with session_factory.begin() as session:
            await barrier.wait()
            return await ensure_usage_partition(
                session,
                year=target_start.year,
                month=target_start.month,
            )

    try:
        assert await asyncio.gather(create_partition(), create_partition()) == [
            partition_name,
            partition_name,
        ]
        async with session_factory() as session:
            partition_count = await session.scalar(
                text(
                    """
                    SELECT count(*)
                    FROM pg_inherits AS inheritance
                    JOIN pg_class AS child ON child.oid = inheritance.inhrelid
                    JOIN pg_class AS parent ON parent.oid = inheritance.inhparent
                    WHERE child.relname = :partition_name
                      AND parent.relname = 'usage_events'
                    """
                ),
                {"partition_name": partition_name},
            )
        assert partition_count == 1
    finally:
        await engine.dispose()


@pytest.mark.integration
async def test_usage_events_route_and_partition_ensure_is_idempotent(
    db_session: AsyncSession,
) -> None:
    current_start, next_start = month_bounds(datetime.now(UTC))
    _, following_start = month_bounds(next_start)

    organization_id = await db_session.scalar(
        text(
            """
            INSERT INTO organizations (public_id, name)
            VALUES (:public_id, :name)
            RETURNING id
            """
        ),
        {"public_id": generate_uuidv7(), "name": "Partition test organization"},
    )
    assert organization_id is not None

    routed_partition = await db_session.scalar(
        text(
            """
            INSERT INTO usage_events (
                public_id,
                organization_id,
                feature_key,
                quantity,
                recorded_at,
                idempotency_key,
                metadata
            )
            VALUES (
                :public_id,
                :organization_id,
                :feature_key,
                :quantity,
                :recorded_at,
                :idempotency_key,
                '{}'::jsonb
            )
            RETURNING tableoid::regclass::text
            """
        ),
        {
            "public_id": generate_uuidv7(),
            "organization_id": organization_id,
            "feature_key": "api_calls",
            "quantity": 1,
            "recorded_at": current_start + timedelta(days=1),
            "idempotency_key": "usage-current-month",
        },
    )
    assert routed_partition == f"usage_events_{current_start:%Y_%m}"

    expected_next_partition = f"usage_events_{next_start:%Y_%m}"
    assert (
        await ensure_usage_partition(
            db_session,
            year=next_start.year,
            month=next_start.month,
        )
        == expected_next_partition
    )
    assert (
        await ensure_usage_partition(
            db_session,
            year=next_start.year,
            month=next_start.month,
        )
        == expected_next_partition
    )
    await db_session.commit()

    with pytest.raises(DBAPIError, match="no partition of relation"):
        async with db_session.begin_nested():
            await db_session.execute(
                text(
                    """
                    INSERT INTO usage_events (
                        public_id,
                        organization_id,
                        feature_key,
                        quantity,
                        recorded_at,
                        idempotency_key,
                        metadata
                    )
                    VALUES (
                        :public_id,
                        :organization_id,
                        :feature_key,
                        :quantity,
                        :recorded_at,
                        :idempotency_key,
                        '{}'::jsonb
                    )
                    """
                ),
                {
                    "public_id": generate_uuidv7(),
                    "organization_id": organization_id,
                    "feature_key": "api_calls",
                    "quantity": 1,
                    "recorded_at": following_start,
                    "idempotency_key": "usage-outside-covered-range",
                },
            )
