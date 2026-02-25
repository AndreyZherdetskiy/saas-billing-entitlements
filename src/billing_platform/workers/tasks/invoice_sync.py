"""Celery task: sync a local invoice to mock Stripe."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import TypeVar

from celery import Task

from billing_platform.db import close_db_engine, get_session_factory
from billing_platform.services.invoice_sync import sync_invoice_to_mock_stripe
from billing_platform.workers.celery_app import celery_app

_T = TypeVar("_T")


def _run_async(coro: Coroutine[object, object, _T]) -> _T:
    """Run async code from Celery's synchronous worker process."""
    return asyncio.run(coro)


async def _run_sync(invoice_id: int) -> dict[str, str]:
    session_factory = get_session_factory()
    try:
        async with session_factory() as session:
            external_id = await sync_invoice_to_mock_stripe(session, invoice_id=invoice_id)
            await session.commit()
            return {"external_invoice_id": external_id}
    finally:
        await close_db_engine()


@celery_app.task(
    name="invoices.sync_mock_stripe",
    bind=True,
    max_retries=3,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
)
def sync_mock_stripe(self: Task[..., dict[str, str]], *, invoice_id: int) -> dict[str, str]:
    """Sync invoice to mock Stripe after domain TX; idempotent by external_invoice_id."""
    return _run_async(_run_sync(invoice_id))
