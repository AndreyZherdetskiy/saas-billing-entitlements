"""Reconciliation run and discrepancy ORM models (ADR-007)."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from billing_platform.domain.ids import generate_uuidv7
from billing_platform.domain.models.base import Base


class ReconciliationRunType(str, enum.Enum):
    """How the reconciliation run was triggered."""

    MANUAL = "manual"
    DAILY = "daily"


class ReconciliationRunStatus(str, enum.Enum):
    """Lifecycle status for a reconciliation run."""

    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ReconciliationDiscrepancyKind(str, enum.Enum):
    """Types of detected mismatches between platform and provider."""

    MISSING_IN_PLATFORM = "missing_in_platform"
    AMOUNT_MISMATCH = "amount_mismatch"
    STATUS_MISMATCH = "status_mismatch"
    MISSING_IN_STRIPE = "missing_in_stripe"
    LEDGER_INVOICE_MISMATCH = "ledger_invoice_mismatch"


class ReconciliationRun(Base):
    """A single reconciliation execution comparing platform vs provider registry."""

    __tablename__ = "reconciliation_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=generate_uuidv7,
    )
    run_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default=ReconciliationRunStatus.RUNNING.value,
    )
    stats: Mapped[dict[str, object]] = mapped_column(
        JSONB,
        nullable=False,
        server_default="{}",
    )
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class ReconciliationDiscrepancy(Base):
    """A detected mismatch recorded during a reconciliation run (detection only)."""

    __tablename__ = "reconciliation_discrepancies"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=generate_uuidv7,
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("reconciliation_runs.id"),
        nullable=False,
        index=True,
    )
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    external_invoice_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    expected_amount_cents: Mapped[int | None] = mapped_column(nullable=True)
    actual_amount_cents: Mapped[int | None] = mapped_column(nullable=True)
    delta_cents: Mapped[int | None] = mapped_column(nullable=True)
    details: Mapped[dict[str, object]] = mapped_column(
        JSONB,
        nullable=False,
        server_default="{}",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
