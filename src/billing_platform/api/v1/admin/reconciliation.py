"""Reconciliation admin HTTP routes (API G)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from billing_platform.api.deps import require_platform_admin
from billing_platform.api.openapi_docs import (
    AUTH_RESPONSES,
    BAD_REQUEST_RESPONSE,
    FORBIDDEN_RESPONSE,
    NOT_FOUND_RESPONSE,
    merge_responses,
)
from billing_platform.db import get_session
from billing_platform.domain.models.reconciliation import (
    ReconciliationDiscrepancy,
    ReconciliationRun,
)
from billing_platform.services.api_keys import AuthContext
from billing_platform.services.reconciliation import (
    get_run_by_id,
    list_discrepancies_for_run,
    list_runs,
    run_reconciliation,
)

router = APIRouter(prefix="/admin/reconciliation", tags=["reconciliation"])

_RUN_ID = Path(description="External UUIDv7 of the reconciliation run (not internal BIGINT id).")


class ReconciliationRunResponse(BaseModel):
    id: UUID = Field(
        description="External UUIDv7 of the reconciliation run (not internal BIGINT id).",
    )
    run_type: str = Field(description="Reconciliation run type (e.g. manual, scheduled).")
    status: str = Field(description="Run status (e.g. running, completed, failed).")
    stats: dict[str, object] = Field(description="Summary statistics from the run.")
    started_at: datetime = Field(description="UTC timestamp when the run started.")
    completed_at: datetime | None = Field(
        default=None,
        description="UTC timestamp when the run completed, if finished.",
    )


class ReconciliationDiscrepancyResponse(BaseModel):
    id: UUID = Field(
        description="External UUIDv7 of the discrepancy record (not internal BIGINT id).",
    )
    run_id: UUID = Field(description="External UUIDv7 of the parent reconciliation run.")
    kind: str = Field(description="Discrepancy kind (e.g. amount_mismatch, missing_invoice).")
    external_invoice_id: str | None = Field(
        default=None,
        description="External provider invoice id, if applicable.",
    )
    expected_amount_cents: int | None = Field(
        default=None,
        description="Expected amount in minor currency units from the ledger.",
    )
    actual_amount_cents: int | None = Field(
        default=None,
        description="Actual amount in minor currency units from the provider.",
    )
    delta_cents: int | None = Field(
        default=None,
        description="Difference between expected and actual amounts in minor units.",
    )
    details: dict[str, object] = Field(description="Additional discrepancy context.")
    created_at: datetime = Field(description="UTC timestamp when the discrepancy was recorded.")


def _run_to_response(run: ReconciliationRun) -> ReconciliationRunResponse:
    return ReconciliationRunResponse(
        id=run.id,
        run_type=run.run_type,
        status=run.status,
        stats=run.stats,
        started_at=run.started_at,
        completed_at=run.completed_at,
    )


def _discrepancy_to_response(
    discrepancy: ReconciliationDiscrepancy,
) -> ReconciliationDiscrepancyResponse:
    return ReconciliationDiscrepancyResponse(
        id=discrepancy.id,
        run_id=discrepancy.run_id,
        kind=discrepancy.kind,
        external_invoice_id=discrepancy.external_invoice_id,
        expected_amount_cents=discrepancy.expected_amount_cents,
        actual_amount_cents=discrepancy.actual_amount_cents,
        delta_cents=discrepancy.delta_cents,
        details=discrepancy.details,
        created_at=discrepancy.created_at,
    )


@router.post(
    "/run",
    response_model=ReconciliationRunResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Trigger reconciliation run",
    description=(
        "Starts a manual reconciliation run comparing platform ledger entries against the "
        "mock Stripe invoice registry. Platform_admin only. "
        "Idempotent via Idempotency-Key header."
    ),
    responses=merge_responses(AUTH_RESPONSES, FORBIDDEN_RESPONSE, BAD_REQUEST_RESPONSE),
)
async def trigger_reconciliation_run(
    session: Annotated[AsyncSession, Depends(get_session)],
    _ctx: Annotated[AuthContext, Depends(require_platform_admin)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> ReconciliationRunResponse:
    """Manually compare platform ledger vs mock Stripe registry."""
    if not idempotency_key.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Idempotency-Key header is required",
        )
    run = await run_reconciliation(
        session,
        run_type="manual",
        idempotency_key=idempotency_key,
    )
    await session.commit()
    return _run_to_response(run)


@router.get(
    "/runs",
    response_model=list[ReconciliationRunResponse],
    summary="List reconciliation runs",
    description=(
        "Lists past reconciliation runs with status and stats, newest first. "
        "Platform_admin only. Paginated via limit (max 200) and offset."
    ),
    responses=merge_responses(AUTH_RESPONSES, FORBIDDEN_RESPONSE),
)
async def list_reconciliation_runs(
    session: Annotated[AsyncSession, Depends(get_session)],
    _ctx: Annotated[AuthContext, Depends(require_platform_admin)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ReconciliationRunResponse]:
    runs = await list_runs(session, limit=limit, offset=offset)
    return [_run_to_response(run) for run in runs]


@router.get(
    "/runs/{run_id}/discrepancies",
    response_model=list[ReconciliationDiscrepancyResponse],
    summary="List run discrepancies",
    description=(
        "Lists amount or presence mismatches found during a reconciliation run. "
        "Platform_admin only. Paginated via limit (max 500) and offset."
    ),
    responses=merge_responses(AUTH_RESPONSES, FORBIDDEN_RESPONSE, NOT_FOUND_RESPONSE),
)
async def list_run_discrepancies(
    run_id: Annotated[UUID, _RUN_ID],
    session: Annotated[AsyncSession, Depends(get_session)],
    _ctx: Annotated[AuthContext, Depends(require_platform_admin)],
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ReconciliationDiscrepancyResponse]:
    run = await get_run_by_id(session, run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="reconciliation run not found",
        )
    discrepancies = await list_discrepancies_for_run(
        session,
        run_id=run_id,
        limit=limit,
        offset=offset,
    )
    return [_discrepancy_to_response(d) for d in discrepancies]
