"""Celery task: close billing period for an organization."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from datetime import UTC, datetime
from typing import TypeVar

from celery import Task

from billing_platform.db import close_db_engine, get_session_factory
from billing_platform.services.period_close import close_billing_period
from billing_platform.workers.celery_app import celery_app
from billing_platform.workers.tasks.invoice_sync import sync_mock_stripe

_T = TypeVar("_T")


def _run_async(coro: Coroutine[object, object, _T]) -> _T:
    """Run async code from Celery's synchronous worker process."""
    return asyncio.run(coro)


async def _run_period_close(
    *,
    organization_id: int,
    period_start: datetime,
    period_end: datetime,
    idempotency_key: str,
) -> dict[str, str | int]:
    session_factory = get_session_factory()
    try:
        async with session_factory() as session:
            result = await close_billing_period(
                session,
                organization_id=organization_id,
                period_start=period_start,
                period_end=period_end,
                idempotency_key=idempotency_key,
            )
            await session.commit()
            sync_mock_stripe.delay(invoice_id=result.invoice_id)
            return {
                "invoice_id": result.invoice_id,
                "invoice_public_id": str(result.invoice_public_id),
                "total_amount_cents": result.total_amount_cents,
                "ledger_entry_public_id": (
                    str(result.ledger_entry_public_id)
                    if result.ledger_entry_public_id is not None
                    else ""
                ),
            }
    finally:
        await close_db_engine()


@celery_app.task(
    name="usage.close_period",
    bind=True,
    max_retries=3,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
)
def close_period(
    self: Task[..., dict[str, str | int]],
    *,
    organization_id: int,
    period_start: str,
    period_end: str,
    idempotency_key: str,
) -> dict[str, str | int]:
    """Close a billing period; safe to retry (idempotent by idempotency_key)."""
    parsed_start = datetime.fromisoformat(period_start)
    parsed_end = datetime.fromisoformat(period_end)
    if parsed_start.tzinfo is None:
        parsed_start = parsed_start.replace(tzinfo=UTC)
    if parsed_end.tzinfo is None:
        parsed_end = parsed_end.replace(tzinfo=UTC)
    return _run_async(
        _run_period_close(
            organization_id=organization_id,
            period_start=parsed_start,
            period_end=parsed_end,
            idempotency_key=idempotency_key,
        )
    )
