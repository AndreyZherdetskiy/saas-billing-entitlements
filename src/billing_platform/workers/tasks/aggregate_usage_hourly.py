"""Celery task: roll up usage events into hourly aggregates."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from datetime import UTC, datetime
from typing import TypeVar

from celery import Task

from billing_platform.db import close_db_engine, get_session_factory
from billing_platform.services.usage import aggregate_hour, aggregate_pending_hours
from billing_platform.workers.celery_app import celery_app

_T = TypeVar("_T")


def _run_async(coro: Coroutine[object, object, _T]) -> _T:
    """Run async code from Celery's synchronous worker process."""
    return asyncio.run(coro)


async def _run_aggregate_hourly(
    *,
    organization_id: int,
    feature_key: str,
    hour_start: datetime,
) -> dict[str, str | int]:
    session_factory = get_session_factory()
    try:
        async with session_factory() as session:
            aggregate = await aggregate_hour(
                session,
                organization_id=organization_id,
                feature_key=feature_key,
                hour_start=hour_start,
            )
            await session.commit()
            return {
                "aggregate_id": aggregate.id,
                "public_id": str(aggregate.public_id),
                "quantity": str(aggregate.quantity),
            }
    finally:
        await close_db_engine()


@celery_app.task(
    name="usage.aggregate_hourly",
    bind=True,
    max_retries=3,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
)
def aggregate_hourly(
    self: Task[..., dict[str, str | int]],
    *,
    organization_id: int,
    feature_key: str,
    hour_start: str,
) -> dict[str, str | int]:
    """Aggregate one hour bucket; safe to retry (UPSERT on unique key)."""
    parsed_hour_start = datetime.fromisoformat(hour_start)
    if parsed_hour_start.tzinfo is None:
        parsed_hour_start = parsed_hour_start.replace(tzinfo=UTC)
    return _run_async(
        _run_aggregate_hourly(
            organization_id=organization_id,
            feature_key=feature_key,
            hour_start=parsed_hour_start,
        )
    )


async def _run_aggregate_hourly_sweep(*, now: datetime) -> dict[str, int]:
    session_factory = get_session_factory()
    try:
        async with session_factory() as session:
            processed = await aggregate_pending_hours(session, now=now)
            await session.commit()
            return {"processed": processed}
    finally:
        await close_db_engine()


@celery_app.task(
    name="usage.aggregate_hourly_sweep",
    bind=True,
    max_retries=3,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
)
def aggregate_hourly_sweep(
    self: Task[..., dict[str, int]],
    *,
    now: str | None = None,
) -> dict[str, int]:
    """Hourly sweep over recent event buckets; delegates to idempotent aggregate_hour."""
    parsed_now = datetime.fromisoformat(now) if now is not None else datetime.now(UTC)
    if parsed_now.tzinfo is None:
        parsed_now = parsed_now.replace(tzinfo=UTC)
    return _run_async(_run_aggregate_hourly_sweep(now=parsed_now))
