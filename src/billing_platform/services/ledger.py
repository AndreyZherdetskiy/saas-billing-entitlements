"""Append-only ledger service (ADR-006)."""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from billing_platform.domain.models.ledger import LedgerEntry, LedgerEntryType
from billing_platform.domain.models.organization import Organization
from billing_platform.domain.models.subscription import Subscription
from billing_platform.observability.metrics import increment_ledger_entries_posted
from billing_platform.services.outbox_hooks import enqueue_outbox

LEDGER_ENTRY_POSTED_EVENT = "ledger.entry_posted"
USAGE_CHARGE_ENTRY_TYPE = LedgerEntryType.usage_charge.value


class LedgerError(Exception):
    """Base ledger service error."""


class LedgerEntryNotFoundError(LedgerError):
    """Referenced ledger entry does not exist."""


class LedgerService:
    """Post and reverse immutable ledger entries in the same TX as domain writes."""

    @staticmethod
    async def post(
        session: AsyncSession,
        *,
        organization_id: int,
        entry_type: str,
        amount_cents: int,
        currency: str,
        idempotency_key: str,
        correlation_id: str,
        subscription_id: int | None = None,
        invoice_id: int | None = None,
        quantity: Decimal | None = None,
        metadata: dict[str, object] | None = None,
    ) -> LedgerEntry:
        """Insert a ledger row idempotently and enqueue ledger.entry_posted."""
        existing = await get_entry_by_idempotency_key(session, idempotency_key)
        if existing is not None:
            return existing

        entry = LedgerEntry(
            organization_id=organization_id,
            subscription_id=subscription_id,
            invoice_id=invoice_id,
            entry_type=entry_type,
            amount_cents=amount_cents,
            currency=currency.upper(),
            quantity=quantity,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
            metadata_=metadata or {},
        )
        session.add(entry)
        await session.flush()

        await _enqueue_entry_posted(
            session,
            entry=entry,
            idempotency_key=f"{idempotency_key}:ledger.entry_posted",
        )
        increment_ledger_entries_posted()
        return entry

    @staticmethod
    async def reverse(
        session: AsyncSession,
        *,
        entry_id: int,
        idempotency_key: str,
        correlation_id: str,
    ) -> LedgerEntry:
        """Create a compensating reversal row; original entry is never deleted."""
        original = await get_entry(session, entry_id)
        if original is None:
            raise LedgerEntryNotFoundError(f"ledger entry {entry_id} not found")

        existing = await get_entry_by_idempotency_key(session, idempotency_key)
        if existing is not None:
            return existing

        reversal = LedgerEntry(
            organization_id=original.organization_id,
            subscription_id=original.subscription_id,
            invoice_id=original.invoice_id,
            entry_type=LedgerEntryType.reversal.value,
            amount_cents=-original.amount_cents,
            currency=original.currency,
            quantity=original.quantity,
            reverses_entry_id=original.id,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
            metadata_={
                "reversed_entry_public_id": str(original.public_id),
                "reversed_entry_type": original.entry_type,
            },
        )
        session.add(reversal)
        await session.flush()

        await _enqueue_entry_posted(
            session,
            entry=reversal,
            idempotency_key=f"{idempotency_key}:ledger.entry_posted",
        )
        return reversal


async def get_entry(session: AsyncSession, entry_id: int) -> LedgerEntry | None:
    """Load a ledger entry by internal BIGINT id."""
    result = await session.execute(select(LedgerEntry).where(LedgerEntry.id == entry_id))
    return result.scalar_one_or_none()


async def get_entry_by_public_id(
    session: AsyncSession,
    entry_public_id: uuid.UUID,
) -> LedgerEntry | None:
    """Load a ledger entry by public UUID."""
    result = await session.execute(
        select(LedgerEntry).where(LedgerEntry.public_id == entry_public_id)
    )
    return result.scalar_one_or_none()


async def get_entry_by_idempotency_key(
    session: AsyncSession,
    idempotency_key: str,
) -> LedgerEntry | None:
    """Load a ledger entry by idempotency key."""
    result = await session.execute(
        select(LedgerEntry).where(LedgerEntry.idempotency_key == idempotency_key)
    )
    return result.scalar_one_or_none()


async def list_entries_for_organization(
    session: AsyncSession,
    *,
    organization_id: int,
    limit: int = 100,
    offset: int = 0,
) -> list[LedgerEntry]:
    """List ledger entries for an organization, newest first."""
    result = await session.execute(
        select(LedgerEntry)
        .where(LedgerEntry.organization_id == organization_id)
        .order_by(LedgerEntry.created_at.desc(), LedgerEntry.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(result.scalars().all())


async def _enqueue_entry_posted(
    session: AsyncSession,
    *,
    entry: LedgerEntry,
    idempotency_key: str,
) -> None:
    org_result = await session.execute(
        select(Organization.public_id).where(Organization.id == entry.organization_id)
    )
    org_public_id = org_result.scalar_one()

    payload: dict[str, object] = {
        "entry_public_id": str(entry.public_id),
        "entry_type": entry.entry_type,
        "amount_cents": entry.amount_cents,
        "currency": entry.currency,
        "organization_public_id": str(org_public_id),
        "correlation_id": entry.correlation_id,
    }

    if entry.subscription_id is not None:
        sub_result = await session.execute(
            select(Subscription.public_id).where(Subscription.id == entry.subscription_id)
        )
        sub_public_id = sub_result.scalar_one_or_none()
        if sub_public_id is not None:
            payload["subscription_public_id"] = str(sub_public_id)

    if entry.reverses_entry_id is not None:
        rev_result = await session.execute(
            select(LedgerEntry.public_id).where(LedgerEntry.id == entry.reverses_entry_id)
        )
        rev_public_id = rev_result.scalar_one_or_none()
        if rev_public_id is not None:
            payload["reverses_entry_public_id"] = str(rev_public_id)

    metadata = entry.metadata_ or {}
    if metadata:
        payload["metadata"] = metadata

    await enqueue_outbox(
        session,
        aggregate_type="ledger",
        aggregate_id=str(entry.public_id),
        event_type=LEDGER_ENTRY_POSTED_EVENT,
        payload=payload,
        idempotency_key=idempotency_key,
        partition_key=str(entry.organization_id),
    )


async def post(
    session: AsyncSession,
    *,
    organization_id: int,
    entry_type: str,
    amount_cents: int,
    currency: str,
    idempotency_key: str,
    correlation_id: str,
    subscription_id: int | None = None,
    invoice_id: int | None = None,
) -> LedgerEntry:
    """Module-level alias for LedgerService.post."""
    return await LedgerService.post(
        session,
        organization_id=organization_id,
        entry_type=entry_type,
        amount_cents=amount_cents,
        currency=currency,
        idempotency_key=idempotency_key,
        correlation_id=correlation_id,
        subscription_id=subscription_id,
        invoice_id=invoice_id,
    )


async def reverse(
    session: AsyncSession,
    *,
    entry_id: int,
    idempotency_key: str,
    correlation_id: str,
) -> LedgerEntry:
    """Module-level alias for LedgerService.reverse."""
    return await LedgerService.reverse(
        session,
        entry_id=entry_id,
        idempotency_key=idempotency_key,
        correlation_id=correlation_id,
    )
