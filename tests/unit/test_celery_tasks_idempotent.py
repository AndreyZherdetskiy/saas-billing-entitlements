"""Parametrized idempotency matrix for Celery beat-scheduled tasks (ADR-004)."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from billing_platform.config import get_settings
from billing_platform.db import close_db_engine
from billing_platform.domain.ids import generate_uuidv7
from billing_platform.domain.models.reconciliation import ReconciliationRun
from billing_platform.domain.models.usage_aggregate import UsageAggregate
from billing_platform.domain.models.usage_event import UsageEvent
from billing_platform.services.reconciliation import get_run_by_idempotency_key
from billing_platform.workers.beat_schedule import build_beat_schedule
from billing_platform.workers.tasks.aggregate_usage_hourly import (
    aggregate_hourly,
    aggregate_hourly_sweep,
)
from billing_platform.workers.tasks.create_usage_partition import month_bounds
from billing_platform.workers.tasks.dunning_steps import process_due_attempts_task
from billing_platform.workers.tasks.enforce_grace_expiry import enforce_grace_expiry_task
from billing_platform.workers.tasks.reconciliation_daily import reconciliation_daily_task
from billing_platform.workers.tasks.usage_partition_celery import create_usage_partition_task

HOUR = datetime(2026, 2, 18, 10, 0, tzinfo=UTC)
RUN_DATE = "2026-02-17"
IDEM_KEY = f"recon:daily:{RUN_DATE}"


@dataclass(frozen=True)
class IdempotentTaskCase:
    task_name: str
    invoke: Callable[..., Any]
    kwargs: dict[str, Any]
    needs_usage_seed: bool = False


@pytest.fixture
def celery_env(monkeypatch: pytest.MonkeyPatch, migrated_postgres_url: str) -> str:
    """Point Celery task DB helpers at the migrated test database."""

    async def _noop_close_redis() -> None:
        return None

    os.environ["DATABASE_URL"] = migrated_postgres_url
    get_settings.cache_clear()
    monkeypatch.setattr(
        "billing_platform.integrations.redis_cache.close_redis_client",
        _noop_close_redis,
    )
    asyncio.run(close_db_engine())
    return migrated_postgres_url


async def _seed_usage_events(database_url: str) -> int:
    from billing_platform.workers.tasks.create_usage_partition import ensure_usage_partition

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
                {"public_id": generate_uuidv7(), "name": "Celery idempotent org"},
            )
            assert org_id is not None

            await ensure_usage_partition(session, year=HOUR.year, month=HOUR.month)
            for index, quantity in enumerate((5, 7)):
                session.add(
                    UsageEvent(
                        public_id=generate_uuidv7(),
                        organization_id=org_id,
                        feature_key="api_calls",
                        quantity=Decimal(quantity),
                        recorded_at=HOUR + timedelta(minutes=index * 10),
                        idempotency_key=f"idem-celery-{index}",
                    )
                )
            await session.commit()
            return org_id
    finally:
        await engine.dispose()


async def _count_aggregates(database_url: str) -> int:
    engine = create_async_engine(database_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with session_factory() as session:
            count = await session.scalar(select(func.count()).select_from(UsageAggregate))
            assert count is not None
            return count
    finally:
        await engine.dispose()


async def _verify_aggregate_hourly(
    database_url: str,
    first: dict[str, str | int],
    second: dict[str, str | int],
) -> None:
    assert first["aggregate_id"] == second["aggregate_id"]
    assert first["quantity"] == second["quantity"]
    assert Decimal(str(first["quantity"])) == Decimal(12)
    assert await _count_aggregates(database_url) == 1


async def _verify_aggregate_sweep(
    database_url: str,
    first: dict[str, int],
    second: dict[str, int],
) -> None:
    assert first["processed"] == second["processed"] == 1
    assert await _count_aggregates(database_url) == 1


async def _verify_create_partition(
    database_url: str,
    first: dict[str, list[str]],
    second: dict[str, list[str]],
) -> None:
    assert first["partition_names"] == second["partition_names"]
    assert len(first["partition_names"]) == 2
    engine = create_async_engine(database_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with session_factory() as session:
            for partition_name in first["partition_names"]:
                exists = await session.scalar(
                    text(
                        """
                        SELECT count(*)
                        FROM pg_class
                        WHERE relname = :partition_name
                        """
                    ),
                    {"partition_name": partition_name},
                )
                assert exists == 1
    finally:
        await engine.dispose()


async def _verify_enforce_grace(
    database_url: str,
    first: dict[str, int],
    second: dict[str, int],
) -> None:
    del database_url
    assert first == second == {"processed": 0}


async def _verify_dunning(
    database_url: str,
    first: dict[str, int],
    second: dict[str, int],
) -> None:
    del database_url
    assert first == second == {"processed": 0}


async def _verify_reconciliation_daily(
    database_url: str,
    first: dict[str, str],
    second: dict[str, str],
) -> None:
    assert first["run_id"] == second["run_id"]
    assert first["idempotency_key"] == second["idempotency_key"] == IDEM_KEY
    engine = create_async_engine(database_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with session_factory() as session:
            run = await get_run_by_idempotency_key(session, idempotency_key=IDEM_KEY)
            assert run is not None
            count = await session.scalar(select(func.count()).select_from(ReconciliationRun))
            assert count == 1
    finally:
        await engine.dispose()


def _empty_stripe_invoices() -> list[dict[str, Any]]:
    return []


IDEMPOTENT_TASK_CASES: tuple[IdempotentTaskCase, ...] = (
    IdempotentTaskCase(
        task_name="usage.aggregate_hourly",
        invoke=aggregate_hourly,
        kwargs={
            "organization_id": 0,
            "feature_key": "api_calls",
            "hour_start": HOUR.isoformat(),
        },
        needs_usage_seed=True,
    ),
    IdempotentTaskCase(
        task_name="usage.aggregate_hourly_sweep",
        invoke=aggregate_hourly_sweep,
        kwargs={"now": (HOUR + timedelta(hours=1)).isoformat()},
        needs_usage_seed=True,
    ),
    IdempotentTaskCase(
        task_name="usage.create_partition",
        invoke=create_usage_partition_task,
        kwargs={},
    ),
    IdempotentTaskCase(
        task_name="subscription.enforce_grace_expiry",
        invoke=enforce_grace_expiry_task,
        kwargs={"now": datetime(2026, 2, 23, 12, 0, tzinfo=UTC).isoformat()},
    ),
    IdempotentTaskCase(
        task_name="dunning.process_due_attempts",
        invoke=process_due_attempts_task,
        kwargs={"now": datetime(2026, 2, 23, 12, 0, tzinfo=UTC).isoformat()},
    ),
    IdempotentTaskCase(
        task_name="reconciliation.daily",
        invoke=reconciliation_daily_task,
        kwargs={"run_date": RUN_DATE},
    ),
)

VERIFY_BY_TASK: dict[str, Callable[..., Any]] = {
    "usage.aggregate_hourly": _verify_aggregate_hourly,
    "usage.aggregate_hourly_sweep": _verify_aggregate_sweep,
    "usage.create_partition": _verify_create_partition,
    "subscription.enforce_grace_expiry": _verify_enforce_grace,
    "dunning.process_due_attempts": _verify_dunning,
    "reconciliation.daily": _verify_reconciliation_daily,
}


@pytest.mark.parametrize(
    "case",
    IDEMPOTENT_TASK_CASES,
    ids=[case.task_name for case in IDEMPOTENT_TASK_CASES],
)
def test_celery_task_double_invoke_is_idempotent(
    case: IdempotentTaskCase,
    celery_env: str,
) -> None:
    """Invoking each beat task twice with the same business key is safe."""
    kwargs = dict(case.kwargs)
    if case.needs_usage_seed:
        org_id = asyncio.run(_seed_usage_events(celery_env))
        if case.task_name == "usage.aggregate_hourly":
            kwargs["organization_id"] = org_id

    recon_patch = patch(
        "billing_platform.services.reconciliation.MockStripeClient.list_invoices",
        new=AsyncMock(return_value=_empty_stripe_invoices()),
    )
    with recon_patch if case.task_name == "reconciliation.daily" else nullcontext():
        first = case.invoke(**kwargs)
        second = case.invoke(**kwargs)

    asyncio.run(VERIFY_BY_TASK[case.task_name](celery_env, first, second))

    asyncio.run(close_db_engine())
    get_settings.cache_clear()


def test_beat_schedule_includes_required_entries() -> None:
    schedule = build_beat_schedule()
    scheduled_tasks = {entry["task"] for entry in schedule.values()}

    assert "usage.aggregate_hourly_sweep" in scheduled_tasks
    assert "subscription.enforce_grace_expiry" in scheduled_tasks
    assert "dunning.process_due_attempts" in scheduled_tasks
    assert "reconciliation.daily" in scheduled_tasks
    assert "usage.create_partition" in scheduled_tasks


def test_beat_schedule_has_no_kafka_publish_surface() -> None:
    """Gate A: beat schedule builder must not import Kafka producers."""
    import inspect

    import billing_platform.workers.beat_schedule as beat_schedule_module

    source = inspect.getsource(beat_schedule_module.build_beat_schedule)
    assert "kafka" not in source.lower()
    assert "producer" not in source.lower()


def test_create_partition_month_bounds_cover_current_and_next() -> None:
    now = datetime(2026, 2, 18, tzinfo=UTC)
    current_start, next_start = month_bounds(now)
    assert current_start == datetime(2026, 2, 1, tzinfo=UTC)
    assert next_start == datetime(2026, 3, 1, tzinfo=UTC)


def test_beat_schedule_ensures_partitions_daily_before_month_boundary() -> None:
    """ADR-011 / §11.3: daily ensure closes gap if monthly job missed mid-month."""
    from celery.schedules import crontab

    schedule = build_beat_schedule()
    partition_schedules = [
        entry["schedule"]
        for entry in schedule.values()
        if entry["task"] == "usage.create_partition"
    ]
    assert len(partition_schedules) >= 2
    has_daily = any(
        isinstance(schedule_entry, crontab) and schedule_entry.day_of_month == set(range(1, 32))
        for schedule_entry in partition_schedules
    )
    assert has_daily
