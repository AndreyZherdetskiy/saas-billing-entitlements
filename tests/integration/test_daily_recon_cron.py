"""Integration: daily reconciliation cron detects ledger↔invoice mismatch."""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from billing_platform.config import get_settings
from billing_platform.db import close_db_engine
from billing_platform.domain.models.invoice import Invoice, InvoiceStatus
from billing_platform.domain.models.ledger import LedgerEntry, LedgerEntryType
from billing_platform.domain.models.reconciliation import ReconciliationDiscrepancy
from billing_platform.services.ledger import LedgerService
from billing_platform.services.organizations import create_organization
from billing_platform.services.reconciliation import get_run_by_idempotency_key, run_reconciliation
from billing_platform.workers.tasks.reconciliation_daily import reconciliation_daily_task

INVOICE_TOTAL_CENTS = 1000
LEDGER_TOTAL_CENTS = 900
RUN_DATE = "2026-02-17"
IDEM_KEY = f"recon:daily:{RUN_DATE}"


async def _seed_divergent_invoice_and_ledger(db_session: AsyncSession) -> Invoice:
    org = await create_organization(
        db_session,
        name="Daily Recon Org",
        external_id=f"ext-daily-recon-{uuid.uuid4().hex[:8]}",
        idempotency_key=f"seed-daily-recon-org-{uuid.uuid4().hex[:8]}",
    )
    period_start = datetime(2026, 2, 1, tzinfo=UTC)
    invoice = Invoice(
        organization_id=org.id,
        status=InvoiceStatus.open.value,
        currency="USD",
        period_start=period_start,
        period_end=period_start + timedelta(days=30),
        total_amount_cents=INVOICE_TOTAL_CENTS,
        idempotency_key=f"inv:daily-recon-{uuid.uuid4().hex[:8]}",
    )
    db_session.add(invoice)
    await db_session.flush()

    await LedgerService.post(
        db_session,
        organization_id=org.id,
        entry_type=LedgerEntryType.usage_charge.value,
        amount_cents=LEDGER_TOTAL_CENTS,
        currency="USD",
        idempotency_key=f"ledger:daily-recon-{uuid.uuid4().hex[:8]}",
        correlation_id="seed-daily-recon",
        invoice_id=invoice.id,
    )
    await db_session.commit()
    return invoice


def _empty_stripe_invoices() -> list[dict[str, Any]]:
    return []


@pytest.mark.integration
async def test_daily_recon_detects_ledger_invoice_mismatch(
    db_session: AsyncSession,
) -> None:
    """Daily run records ledger_invoice_mismatch for seeded divergent totals."""
    invoice = await _seed_divergent_invoice_and_ledger(db_session)

    ledger_count_before = await db_session.scalar(select(func.count()).select_from(LedgerEntry))
    invoice_total_before = invoice.total_amount_cents

    with patch(
        "billing_platform.services.reconciliation.MockStripeClient.list_invoices",
        new=AsyncMock(return_value=_empty_stripe_invoices()),
    ):
        run = await run_reconciliation(
            db_session,
            run_type="daily",
            idempotency_key=IDEM_KEY,
        )
        await db_session.commit()

    assert run.run_type == "daily"
    assert run.status == "completed"

    discrepancies = await db_session.scalars(
        select(ReconciliationDiscrepancy).where(ReconciliationDiscrepancy.run_id == run.id)
    )
    ledger_mismatches = [d for d in discrepancies.all() if d.kind == "ledger_invoice_mismatch"]
    assert len(ledger_mismatches) >= 1
    mismatch = ledger_mismatches[0]
    assert mismatch.expected_amount_cents == INVOICE_TOTAL_CENTS
    assert mismatch.actual_amount_cents == LEDGER_TOTAL_CENTS
    assert mismatch.delta_cents == INVOICE_TOTAL_CENTS - LEDGER_TOTAL_CENTS

    ledger_count_after = await db_session.scalar(select(func.count()).select_from(LedgerEntry))
    assert ledger_count_after == ledger_count_before

    refreshed_invoice = await db_session.get(Invoice, invoice.id)
    assert refreshed_invoice is not None
    assert refreshed_invoice.total_amount_cents == invoice_total_before


@pytest.mark.integration
async def test_daily_recon_rerun_same_idempotency_key_is_idempotent(
    db_session: AsyncSession,
) -> None:
    """Same daily idempotency key returns existing run without duplicate discrepancies."""
    await _seed_divergent_invoice_and_ledger(db_session)

    with patch(
        "billing_platform.services.reconciliation.MockStripeClient.list_invoices",
        new=AsyncMock(return_value=_empty_stripe_invoices()),
    ):
        first = await run_reconciliation(
            db_session,
            run_type="daily",
            idempotency_key=IDEM_KEY,
        )
        await db_session.commit()

        disc_count_first = await db_session.scalar(
            select(func.count())
            .select_from(ReconciliationDiscrepancy)
            .where(ReconciliationDiscrepancy.run_id == first.id)
        )

        second = await run_reconciliation(
            db_session,
            run_type="daily",
            idempotency_key=IDEM_KEY,
        )
        await db_session.commit()

    assert first.id == second.id

    disc_count_second = await db_session.scalar(
        select(func.count())
        .select_from(ReconciliationDiscrepancy)
        .where(ReconciliationDiscrepancy.run_id == second.id)
    )
    assert disc_count_first == disc_count_second


@pytest.mark.integration
def test_daily_recon_celery_task_invokes_daily_run(migrated_postgres_url: str) -> None:
    """Celery task uses recon:daily:YYYY-MM-DD idempotency key and completes."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    async def _seed() -> None:
        engine = create_async_engine(migrated_postgres_url, pool_pre_ping=True)
        session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        try:
            async with session_factory() as session:
                await _seed_divergent_invoice_and_ledger(session)
        finally:
            await engine.dispose()

    asyncio.run(_seed())

    os.environ["DATABASE_URL"] = migrated_postgres_url
    get_settings.cache_clear()
    asyncio.run(close_db_engine())

    with patch(
        "billing_platform.services.reconciliation.MockStripeClient.list_invoices",
        new=AsyncMock(return_value=_empty_stripe_invoices()),
    ):
        result = reconciliation_daily_task(run_date=RUN_DATE)

    assert result["run_type"] == "daily"
    assert result["idempotency_key"] == IDEM_KEY

    async def _verify() -> None:
        engine = create_async_engine(migrated_postgres_url, pool_pre_ping=True)
        session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        try:
            async with session_factory() as session:
                run = await get_run_by_idempotency_key(session, IDEM_KEY)
                assert run is not None
                assert run.run_type == "daily"
        finally:
            await engine.dispose()

    asyncio.run(_verify())
    asyncio.run(close_db_engine())
    get_settings.cache_clear()
