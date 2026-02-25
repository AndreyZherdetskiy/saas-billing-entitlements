"""Invoice and line item ORM models (dual-id per ADR-010)."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import BigInteger, DateTime, ForeignKey, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from billing_platform.domain.ids import generate_uuidv7
from billing_platform.domain.models.base import Base, DualIdMixin


class InvoiceStatus(StrEnum):
    draft = "draft"
    open = "open"
    paid = "paid"
    void = "void"
    uncollectible = "uncollectible"


class Invoice(Base, DualIdMixin):
    """Financial document for a billing period (dual-id)."""

    __tablename__ = "invoices"

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
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="draft")
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    total_amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    external_invoice_id: Mapped[str | None] = mapped_column(String(255), unique=True)
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class InvoiceLineItem(Base):
    __tablename__ = "invoice_line_items"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=generate_uuidv7)
    invoice_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("invoices.id"),
        nullable=False,
        index=True,
    )
    description: Mapped[str] = mapped_column(String(512), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    unit_amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    feature_key: Mapped[str | None] = mapped_column(String(255))
    price_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("prices.id"))
    usage_period_id: Mapped[uuid.UUID | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
