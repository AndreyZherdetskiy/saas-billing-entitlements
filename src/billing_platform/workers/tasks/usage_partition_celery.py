"""Celery task: ensure monthly usage-event partitions exist."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import TypeVar

from celery import Task

from billing_platform.db import close_db_engine, get_session_factory
from billing_platform.workers.celery_app import celery_app
from billing_platform.workers.tasks.create_usage_partition import (
    ensure_current_and_next_partitions,
    ensure_usage_partition,
)

_T = TypeVar("_T")


def _run_async(coro: Coroutine[object, object, _T]) -> _T:
    """Run async code from Celery's synchronous worker process."""
    return asyncio.run(coro)


async def _run_create_partition(
    *,
    year: int | None,
    month: int | None,
) -> dict[str, list[str]]:
    session_factory = get_session_factory()
    try:
        async with session_factory() as session:
            if year is not None and month is not None:
                partition_name = await ensure_usage_partition(
                    session,
                    year=year,
                    month=month,
                )
                partition_names = [partition_name]
            else:
                partition_names = await ensure_current_and_next_partitions(session)
            await session.commit()
            return {"partition_names": partition_names}
    finally:
        await close_db_engine()


@celery_app.task(
    name="usage.create_partition",
    bind=True,
    max_retries=3,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
)
def create_usage_partition_task(
    self: Task[..., dict[str, list[str]]],
    *,
    year: int | None = None,
    month: int | None = None,
) -> dict[str, list[str]]:
    """Create monthly usage-event partitions; idempotent (IF NOT EXISTS)."""
    return _run_async(_run_create_partition(year=year, month=month))
