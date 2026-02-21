"""Reconciliation service — detection-only comparison (ADR-007)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from billing_platform.config import get_settings
from billing_platform.domain.models.invoice import Invoice
from billing_platform.domain.models.ledger import LedgerEntry, LedgerEntryType
from billing_platform.domain.models.reconciliation import (
    ReconciliationDiscrepancy,
    ReconciliationDiscrepancyKind,
    ReconciliationRun,
    ReconciliationRunStatus,
)
from billing_platform.integrations.mock_stripe.client import MockStripeClient
from billing_platform.integrations.payment_provider import PaymentProviderPort
from billing_platform.observability.alerts import should_alert_recon_mismatch
from billing_platform.observability.metrics import record_reconciliation_discrepancy_amount_cents
from billing_platform.services.outbox_hooks import enqueue_outbox

RECONCILIATION_COMPLETED_EVENT = "reconciliation.completed"
RECONCILIATION_MISMATCH_EVENT = "reconciliation.mismatch"


@dataclass(frozen=True, slots=True)
class AmountMismatchResult:
    """Pure comparison result for invoice amount differences."""

    kind: Literal["amount_mismatch"] = "amount_mismatch"
    expected_cents: int = 0
    actual_cents: int = 0
    delta_cents: int = 0


@dataclass(frozen=True, slots=True)
class DiscrepancyDraft:
    """Pure ledger↔invoice comparison result before persistence."""

    kind: Literal["ledger_invoice_mismatch"]
    expected_amount_cents: int
    actual_amount_cents: int
    delta_cents: int
    external_invoice_id: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


def compare_ledger_to_invoice(
    *,
    ledger_total_cents: int,
    invoice_total_cents: int,
) -> DiscrepancyDraft | None:
    """Return ledger_invoice_mismatch when totals diverge; None when equal."""
    if ledger_total_cents == invoice_total_cents:
        return None
    return DiscrepancyDraft(
        kind="ledger_invoice_mismatch",
        expected_amount_cents=invoice_total_cents,
        actual_amount_cents=ledger_total_cents,
        delta_cents=invoice_total_cents - ledger_total_cents,
    )


@dataclass(frozen=True, slots=True)
class PlatformInvoiceRecord:
    """Aggregated platform-side view for one external invoice id."""

    amount_cents: int
    currency: str
    status: str
    organization_id: int


def compare_amounts(*, expected_cents: int, actual_cents: int) -> AmountMismatchResult | None:
    """Return an amount_mismatch fact when cents differ; None when they match."""
    if expected_cents == actual_cents:
        return None
    return AmountMismatchResult(
        expected_cents=expected_cents,
        actual_cents=actual_cents,
        delta_cents=expected_cents - actual_cents,
    )


def _stripe_invoice_amount_cents(invoice: dict[str, Any]) -> int:
    status = invoice.get("status")
    if status == "paid":
        amount_paid = invoice.get("amount_paid")
        if isinstance(amount_paid, int):
            return amount_paid
    amount_due = invoice.get("amount_due")
    if isinstance(amount_due, int):
        return amount_due
    return 0


def _stripe_invoice_status(invoice: dict[str, Any]) -> str:
    status = invoice.get("status")
    return status if isinstance(status, str) else "unknown"


async def _load_platform_invoice_index(
    session: AsyncSession,
) -> dict[str, PlatformInvoiceRecord]:
    """Build external_invoice_id → platform ledger view (read-only)."""
    result = await session.execute(
        select(LedgerEntry).where(LedgerEntry.entry_type == LedgerEntryType.invoice_paid.value)
    )
    index: dict[str, PlatformInvoiceRecord] = {}
    for entry in result.scalars().all():
        metadata = entry.metadata_ or {}
        external_id = metadata.get("invoice_external_id")
        if not isinstance(external_id, str) or not external_id:
            continue
        index[external_id] = PlatformInvoiceRecord(
            amount_cents=entry.amount_cents,
            currency=entry.currency,
            status="paid",
            organization_id=entry.organization_id,
        )
    return index


async def _fetch_stripe_invoices(
    client: PaymentProviderPort | None = None,
) -> list[dict[str, Any]]:
    stripe_client = client or MockStripeClient()
    raw = await stripe_client.list_invoices()
    return [dict(item) for item in raw]


def _detect_discrepancies(
    *,
    stripe_invoices: list[dict[str, Any]],
    platform_index: dict[str, PlatformInvoiceRecord],
) -> list[dict[str, Any]]:
    """Compare registries and return discrepancy payloads (no DB writes)."""
    discrepancies: list[dict[str, Any]] = []
    seen_stripe_ids: set[str] = set()

    for invoice in stripe_invoices:
        invoice_id = invoice.get("id")
        if not isinstance(invoice_id, str) or not invoice_id:
            continue
        seen_stripe_ids.add(invoice_id)

        platform_record = platform_index.get(invoice_id)
        if platform_record is None:
            discrepancies.append(
                {
                    "kind": ReconciliationDiscrepancyKind.MISSING_IN_PLATFORM.value,
                    "external_invoice_id": invoice_id,
                    "expected_amount_cents": _stripe_invoice_amount_cents(invoice),
                    "actual_amount_cents": None,
                    "delta_cents": None,
                    "details": {
                        "stripe_status": _stripe_invoice_status(invoice),
                        "currency": invoice.get("currency"),
                    },
                }
            )
            continue

        stripe_amount = _stripe_invoice_amount_cents(invoice)
        amount_mismatch = compare_amounts(
            expected_cents=stripe_amount,
            actual_cents=platform_record.amount_cents,
        )
        if amount_mismatch is not None:
            discrepancies.append(
                {
                    "kind": ReconciliationDiscrepancyKind.AMOUNT_MISMATCH.value,
                    "external_invoice_id": invoice_id,
                    "expected_amount_cents": amount_mismatch.expected_cents,
                    "actual_amount_cents": amount_mismatch.actual_cents,
                    "delta_cents": amount_mismatch.delta_cents,
                    "details": {
                        "stripe_status": _stripe_invoice_status(invoice),
                        "platform_status": platform_record.status,
                        "currency": platform_record.currency,
                        "organization_id": platform_record.organization_id,
                    },
                }
            )

        stripe_status = _stripe_invoice_status(invoice)
        if stripe_status != platform_record.status:
            discrepancies.append(
                {
                    "kind": ReconciliationDiscrepancyKind.STATUS_MISMATCH.value,
                    "external_invoice_id": invoice_id,
                    "expected_amount_cents": stripe_amount,
                    "actual_amount_cents": platform_record.amount_cents,
                    "delta_cents": None,
                    "details": {
                        "stripe_status": stripe_status,
                        "platform_status": platform_record.status,
                        "organization_id": platform_record.organization_id,
                    },
                }
            )

    for external_id, platform_record in platform_index.items():
        if external_id not in seen_stripe_ids:
            discrepancies.append(
                {
                    "kind": ReconciliationDiscrepancyKind.MISSING_IN_STRIPE.value,
                    "external_invoice_id": external_id,
                    "expected_amount_cents": platform_record.amount_cents,
                    "actual_amount_cents": None,
                    "delta_cents": None,
                    "details": {
                        "platform_status": platform_record.status,
                        "currency": platform_record.currency,
                        "organization_id": platform_record.organization_id,
                    },
                }
            )

    return discrepancies


async def _sum_ledger_entries_for_invoice(
    session: AsyncSession,
    *,
    invoice_id: int,
    entry_type: str,
) -> int:
    result = await session.execute(
        select(func.coalesce(func.sum(LedgerEntry.amount_cents), 0)).where(
            LedgerEntry.invoice_id == invoice_id,
            LedgerEntry.entry_type == entry_type,
        )
    )
    return int(result.scalar_one())


async def _ledger_total_for_invoice(
    session: AsyncSession,
    invoice: Invoice,
) -> int:
    """Sum ledger charge entries linked to an invoice (usage_charge preferred)."""
    usage_total = await _sum_ledger_entries_for_invoice(
        session,
        invoice_id=invoice.id,
        entry_type=LedgerEntryType.usage_charge.value,
    )
    if usage_total > 0:
        return usage_total

    paid_total = await _sum_ledger_entries_for_invoice(
        session,
        invoice_id=invoice.id,
        entry_type=LedgerEntryType.invoice_paid.value,
    )
    if paid_total > 0:
        return paid_total

    if invoice.external_invoice_id:
        result = await session.execute(
            select(LedgerEntry).where(
                LedgerEntry.entry_type == LedgerEntryType.invoice_paid.value,
            )
        )
        for entry in result.scalars().all():
            metadata = entry.metadata_ or {}
            external_id = metadata.get("invoice_external_id")
            if external_id == invoice.external_invoice_id:
                return entry.amount_cents

    return 0


async def _detect_ledger_invoice_discrepancies(
    session: AsyncSession,
) -> list[dict[str, Any]]:
    """Compare platform invoice totals vs related ledger sums (detection only)."""
    result = await session.execute(select(Invoice))
    invoices = list(result.scalars().all())
    discrepancies: list[dict[str, Any]] = []

    for invoice in invoices:
        ledger_total = await _ledger_total_for_invoice(session, invoice)
        draft = compare_ledger_to_invoice(
            ledger_total_cents=ledger_total,
            invoice_total_cents=invoice.total_amount_cents,
        )
        if draft is None:
            continue
        discrepancies.append(
            {
                "kind": ReconciliationDiscrepancyKind.LEDGER_INVOICE_MISMATCH.value,
                "external_invoice_id": invoice.external_invoice_id,
                "expected_amount_cents": draft.expected_amount_cents,
                "actual_amount_cents": draft.actual_amount_cents,
                "delta_cents": draft.delta_cents,
                "details": {
                    "invoice_public_id": str(invoice.public_id),
                    "organization_id": invoice.organization_id,
                    "invoice_status": invoice.status,
                    "currency": invoice.currency,
                    **draft.details,
                },
            }
        )

    return discrepancies


async def get_run_by_idempotency_key(
    session: AsyncSession,
    idempotency_key: str,
) -> ReconciliationRun | None:
    result = await session.execute(
        select(ReconciliationRun).where(ReconciliationRun.idempotency_key == idempotency_key)
    )
    return result.scalar_one_or_none()


async def get_run_by_id(session: AsyncSession, run_id: uuid.UUID) -> ReconciliationRun | None:
    result = await session.execute(select(ReconciliationRun).where(ReconciliationRun.id == run_id))
    return result.scalar_one_or_none()


async def list_runs(
    session: AsyncSession,
    *,
    limit: int = 50,
    offset: int = 0,
) -> list[ReconciliationRun]:
    result = await session.execute(
        select(ReconciliationRun)
        .order_by(ReconciliationRun.started_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(result.scalars().all())


async def list_discrepancies_for_run(
    session: AsyncSession,
    *,
    run_id: uuid.UUID,
    limit: int = 100,
    offset: int = 0,
) -> list[ReconciliationDiscrepancy]:
    result = await session.execute(
        select(ReconciliationDiscrepancy)
        .where(ReconciliationDiscrepancy.run_id == run_id)
        .order_by(ReconciliationDiscrepancy.created_at.asc())
        .limit(limit)
        .offset(offset)
    )
    return list(result.scalars().all())


async def run_reconciliation(
    session: AsyncSession,
    *,
    run_type: Literal["manual", "daily"] = "manual",
    idempotency_key: str,
    stripe_client: PaymentProviderPort | None = None,
) -> ReconciliationRun:
    """Run reconciliation; idempotent on idempotency_key.

    Read-only with respect to invoices and ledger — only writes run/discrepancy rows.
    """
    existing = await get_run_by_idempotency_key(session, idempotency_key)
    if existing is not None:
        return existing

    run = ReconciliationRun(
        run_type=run_type,
        status=ReconciliationRunStatus.RUNNING.value,
        idempotency_key=idempotency_key,
        stats={},
    )
    session.add(run)
    await session.flush()

    try:
        stripe_invoices = await _fetch_stripe_invoices(stripe_client)
        platform_index = await _load_platform_invoice_index(session)
        stripe_discrepancies = _detect_discrepancies(
            stripe_invoices=stripe_invoices,
            platform_index=platform_index,
        )
        ledger_invoice_discrepancies = await _detect_ledger_invoice_discrepancies(session)
        discrepancy_payloads = stripe_discrepancies + ledger_invoice_discrepancies

        for payload in discrepancy_payloads:
            session.add(
                ReconciliationDiscrepancy(
                    run_id=run.id,
                    kind=str(payload["kind"]),
                    external_invoice_id=(
                        str(payload["external_invoice_id"])
                        if payload.get("external_invoice_id") is not None
                        else None
                    ),
                    expected_amount_cents=(
                        int(payload["expected_amount_cents"])
                        if payload.get("expected_amount_cents") is not None
                        else None
                    ),
                    actual_amount_cents=(
                        int(payload["actual_amount_cents"])
                        if payload.get("actual_amount_cents") is not None
                        else None
                    ),
                    delta_cents=(
                        int(payload["delta_cents"])
                        if payload.get("delta_cents") is not None
                        else None
                    ),
                    details=dict(payload.get("details") or {}),
                )
            )

        total_delta = sum(
            abs(int(d["delta_cents"]))
            for d in discrepancy_payloads
            if d.get("delta_cents") is not None
        )
        run.stats = {
            "stripe_invoice_count": len(stripe_invoices),
            "platform_invoice_count": len(platform_index),
            "discrepancy_count": len(discrepancy_payloads),
            "ledger_invoice_discrepancy_count": len(ledger_invoice_discrepancies),
            "total_delta_cents": total_delta,
        }
        run.status = ReconciliationRunStatus.COMPLETED.value
        run.completed_at = datetime.now(UTC)
        await session.flush()

        record_reconciliation_discrepancy_amount_cents(total_delta)
        await _enqueue_run_events(session, run=run, discrepancy_payloads=discrepancy_payloads)
    except Exception:
        run.status = ReconciliationRunStatus.FAILED.value
        run.completed_at = datetime.now(UTC)
        await session.flush()
        raise

    return run


async def _enqueue_run_events(
    session: AsyncSession,
    *,
    run: ReconciliationRun,
    discrepancy_payloads: list[dict[str, object]],
) -> None:
    run_id = str(run.id)
    summary = {
        "run_id": run_id,
        "run_type": run.run_type,
        "stats": run.stats,
    }
    await enqueue_outbox(
        session,
        aggregate_type="reconciliation",
        aggregate_id=run_id,
        event_type=RECONCILIATION_COMPLETED_EVENT,
        payload={**summary, "organization_id": 0},
        idempotency_key=f"reconciliation:{run_id}:completed",
        partition_key=run_id,
    )

    settings = get_settings()
    alert_threshold = settings.reconciliation_alert_amount_cents
    material: list[dict[str, Any]] = []
    for payload in discrepancy_payloads:
        delta_cents = payload.get("delta_cents")
        if isinstance(delta_cents, int) and should_alert_recon_mismatch(
            delta_cents,
            threshold_cents=alert_threshold,
        ):
            material.append(payload)
    if material:
        await enqueue_outbox(
            session,
            aggregate_type="reconciliation",
            aggregate_id=run_id,
            event_type=RECONCILIATION_MISMATCH_EVENT,
            payload={
                **summary,
                "organization_id": 0,
                "material_discrepancy_count": len(material),
                "alert_threshold_cents": alert_threshold,
            },
            idempotency_key=f"reconciliation:{run_id}:mismatch",
            partition_key=run_id,
        )
