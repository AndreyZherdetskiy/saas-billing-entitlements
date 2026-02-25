"""Append-only ledger entry ORM model (ADR-006, dual-id per ADR-010)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import BigInteger, DateTime, ForeignKey, Numeric, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from billing_platform.domain.models.base import Base, DualIdMixin


class LedgerEntryType(StrEnum):
    trial_grant = "trial_grant"
    invoice_paid = "invoice_paid"
    usage_charge = "usage_charge"
    proration_debit = "proration_debit"
    proration_credit = "proration_credit"
    credit_note = "credit_note"
    access_revoked_marker = "access_revoked_marker"
    reversal = "reversal"
    dunning_fee = "dunning_fee"


class LedgerEntry(Base, DualIdMixin):
    """Immutable ledger row — INSERT-only in application code."""

    __tablename__ = "ledger_entries"

    organization_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("organizations.id"),
        nullable=False,
        index=True,
    )
    subscription_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("subscriptions.id"),
        nullable=True,
        index=True,
    )
    invoice_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    entry_type: Mapped[str] = mapped_column(String(64), nullable=False)
    amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    reverses_entry_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("ledger_entries.id"),
        nullable=True,
    )
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(255), nullable=False)
    metadata_: Mapped[dict[str, object]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        server_default="{}",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
