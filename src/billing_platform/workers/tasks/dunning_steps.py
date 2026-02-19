"""Celery task: process due dunning attempts."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from datetime import UTC, datetime
from typing import TypeVar

from celery import Task

from billing_platform.db import close_db_engine, get_session_factory
from billing_platform.integrations.redis_cache import close_redis_client
from billing_platform.services.dunning import process_due_attempts
from billing_platform.workers.celery_app import celery_app

_T = TypeVar("_T")


def _run_async(coro: Coroutine[object, object, _T]) -> _T:
    """Run async code from Celery's synchronous worker process."""
    return asyncio.run(coro)


async def _run_process_due_attempts(*, now: datetime) -> dict[str, int]:
    session_factory = get_session_factory()
    try:
        async with session_factory() as session:
            processed = await process_due_attempts(session, now=now)
            await session.commit()
            return {"processed": processed}
    finally:
        await close_redis_client()
        await close_db_engine()


@celery_app.task(
    name="dunning.process_due_attempts",
    bind=True,
    max_retries=3,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
)
def process_due_attempts_task(
    self: Task[..., dict[str, int]],
    *,
    now: str | None = None,
) -> dict[str, int]:
    """Execute due dunning attempts; idempotent per attempt row."""
    parsed_now = datetime.fromisoformat(now) if now is not None else datetime.now(UTC)
    if parsed_now.tzinfo is None:
        parsed_now = parsed_now.replace(tzinfo=UTC)
    return _run_async(_run_process_due_attempts(now=parsed_now))
