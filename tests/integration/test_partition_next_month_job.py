"""Integration: usage.create_partition ensures next-month partition."""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from billing_platform.config import get_settings
from billing_platform.db import close_db_engine
from billing_platform.workers.tasks.create_usage_partition import month_bounds
from billing_platform.workers.tasks.usage_partition_celery import create_usage_partition_task


@pytest.mark.integration
def test_create_partition_job_ensures_next_month_partition(migrated_postgres_url: str) -> None:
    """Dropping next-month partition then running the job must recreate it (not only current)."""
    now = datetime.now(UTC)
    _, next_start = month_bounds(now)
    next_partition = f"usage_events_{next_start:%Y_%m}"

    async def _drop_next_month() -> None:
        engine = create_async_engine(migrated_postgres_url, pool_pre_ping=True)
        session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        try:
            async with session_factory() as session:
                await session.execute(text(f"DROP TABLE IF EXISTS {next_partition}"))
                await session.commit()
                exists = await session.scalar(
                    text("SELECT count(*) FROM pg_class WHERE relname = :name"),
                    {"name": next_partition},
                )
                assert exists == 0
        finally:
            await engine.dispose()

    asyncio.run(_drop_next_month())

    os.environ["DATABASE_URL"] = migrated_postgres_url
    get_settings.cache_clear()
    asyncio.run(close_db_engine())

    result = create_usage_partition_task()

    assert next_partition in result["partition_names"]
    assert len(result["partition_names"]) == 2

    async def _verify() -> None:
        engine = create_async_engine(migrated_postgres_url, pool_pre_ping=True)
        session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        try:
            async with session_factory() as session:
                exists = await session.scalar(
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
                    {"partition_name": next_partition},
                )
                assert exists == 1
        finally:
            await engine.dispose()

    asyncio.run(_verify())
    asyncio.run(close_db_engine())
    get_settings.cache_clear()
