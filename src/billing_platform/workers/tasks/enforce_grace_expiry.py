"""Celery task: enforce grace expiry for past_due subscriptions."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from datetime import UTC, datetime
from typing import TypeVar

from celery import Task

from billing_platform.db import close_db_engine, get_session_factory
from billing_platform.integrations.redis_cache import (
    close_redis_client,
    get_redis_client,
    increment_entitlement_version,
)
from billing_platform.services.grace import enforce_grace_expiry
from billing_platform.workers.celery_app import celery_app

_T = TypeVar("_T")


def _run_async(coro: Coroutine[object, object, _T]) -> _T:
    """Run async code from Celery's synchronous worker process."""
    return asyncio.run(coro)


async def _run_enforce_grace_expiry(*, now: datetime) -> dict[str, int]:
    session_factory = get_session_factory()
    try:
        async with session_factory() as session:
            processed, org_ids_to_bump = await enforce_grace_expiry(session, now=now)
            await session.commit()
        if org_ids_to_bump:
            redis = await get_redis_client()
            for organization_id in org_ids_to_bump:
                await increment_entitlement_version(redis, organization_id=organization_id)
        return {"processed": processed}
    finally:
        await close_redis_client()
        await close_db_engine()


@celery_app.task(
    name="subscription.enforce_grace_expiry",
    bind=True,
    max_retries=3,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
)
def enforce_grace_expiry_task(
    self: Task[..., dict[str, int]],
    *,
    now: str | None = None,
) -> dict[str, int]:
    """Revoke access for subscriptions past grace; safe to retry (idempotent keys)."""
    parsed_now = datetime.fromisoformat(now) if now is not None else datetime.now(UTC)
    if parsed_now.tzinfo is None:
        parsed_now = parsed_now.replace(tzinfo=UTC)
    return _run_async(_run_enforce_grace_expiry(now=parsed_now))
