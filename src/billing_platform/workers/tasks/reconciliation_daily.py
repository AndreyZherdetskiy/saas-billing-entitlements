"""Celery task: daily reconciliation cron (ledger↔invoice + Stripe registry)."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from datetime import UTC, date, datetime
from typing import TypeVar

from celery import Task

from billing_platform.db import close_db_engine, get_session_factory
from billing_platform.integrations.redis_cache import close_redis_client
from billing_platform.services.reconciliation import run_reconciliation
from billing_platform.workers.celery_app import celery_app

_T = TypeVar("_T")


def _run_async(coro: Coroutine[object, object, _T]) -> _T:
    """Run async code from Celery's synchronous worker process."""
    return asyncio.run(coro)


def _daily_idempotency_key(run_date: date) -> str:
    return f"recon:daily:{run_date.isoformat()}"


async def _run_daily_reconciliation(*, run_date: date) -> dict[str, str]:
    session_factory = get_session_factory()
    idempotency_key = _daily_idempotency_key(run_date)
    try:
        async with session_factory() as session:
            run = await run_reconciliation(
                session,
                run_type="daily",
                idempotency_key=idempotency_key,
            )
            await session.commit()
            return {
                "run_id": str(run.id),
                "run_type": run.run_type,
                "status": run.status,
                "idempotency_key": idempotency_key,
            }
    finally:
        await close_redis_client()
        await close_db_engine()


@celery_app.task(
    name="reconciliation.daily",
    bind=True,
    max_retries=3,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
)
def reconciliation_daily_task(
    self: Task[..., dict[str, str]],
    *,
    run_date: str | None = None,
) -> dict[str, str]:
    """Run daily reconciliation; idempotent per UTC date (recon:daily:YYYY-MM-DD)."""
    if run_date is not None:
        parsed_date = date.fromisoformat(run_date)
    else:
        parsed_date = datetime.now(UTC).date()
    return _run_async(_run_daily_reconciliation(run_date=parsed_date))
