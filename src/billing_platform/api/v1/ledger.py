"""Ledger read HTTP routes (API H)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from billing_platform.api.deps import assert_ledger_read_access, get_auth_context
from billing_platform.api.openapi_docs import (
    AUTH_RESPONSES,
    FORBIDDEN_RESPONSE,
    NOT_FOUND_RESPONSE,
    merge_responses,
)
from billing_platform.db import get_session
from billing_platform.domain.models.ledger import LedgerEntry
from billing_platform.domain.models.organization import Organization
from billing_platform.domain.models.subscription import Subscription
from billing_platform.services.api_keys import AuthContext
from billing_platform.services.ledger import get_entry_by_public_id, list_entries_for_organization
from billing_platform.services.organizations import get_organization_by_public_id

router = APIRouter(tags=["ledger"])

_ORG_PUBLIC_ID = Path(description="External UUIDv7 of the organization (not internal BIGINT id).")
_ENTRY_PUBLIC_ID = Path(
    description="External UUIDv7 of the ledger entry (not internal BIGINT id).",
)


class LedgerEntryResponse(BaseModel):
    """Public ledger entry representation (no internal BIGINT id)."""

    public_id: UUID = Field(
        description="External UUIDv7 of the ledger entry (not internal BIGINT id).",
    )
    organization_public_id: UUID = Field(
        description="External UUIDv7 of the organization (not internal BIGINT id).",
    )
    entry_type: str = Field(description="Ledger entry type (e.g. charge, credit, reversal).")
    amount_cents: int = Field(description="Signed amount in minor currency units.")
    currency: str = Field(description="ISO 4217 currency code.")
    subscription_public_id: UUID | None = Field(
        default=None,
        description="External UUIDv7 of the related subscription, if any.",
    )
    reverses_entry_public_id: UUID | None = Field(
        default=None,
        description="External UUIDv7 of the entry this one reverses, if any.",
    )
    correlation_id: str = Field(description="Caller or system correlation id for tracing.")
    metadata: dict[str, object] = Field(
        default_factory=dict,
        description="Arbitrary JSON metadata stored with the entry.",
    )
    created_at: datetime = Field(description="UTC timestamp when the entry was recorded.")


def _to_response(
    entry: LedgerEntry,
    *,
    organization_public_id: UUID,
    subscription_public_id: UUID | None = None,
    reverses_entry_public_id: UUID | None = None,
) -> LedgerEntryResponse:
    return LedgerEntryResponse(
        public_id=entry.public_id,
        organization_public_id=organization_public_id,
        entry_type=entry.entry_type,
        amount_cents=entry.amount_cents,
        currency=entry.currency,
        subscription_public_id=subscription_public_id,
        reverses_entry_public_id=reverses_entry_public_id,
        correlation_id=entry.correlation_id,
        metadata=entry.metadata_,
        created_at=entry.created_at,
    )


async def _load_organization_by_id(
    session: AsyncSession,
    organization_id: int,
) -> Organization | None:
    result = await session.execute(select(Organization).where(Organization.id == organization_id))
    return result.scalar_one_or_none()


@router.get(
    "/organizations/{organization_public_id}/ledger",
    response_model=list[LedgerEntryResponse],
    summary="List organization ledger entries",
    description=(
        "Returns append-only ledger entries for an organization, newest first. "
        "Requires platform_admin, revops_read, or a tenant key scoped to that organization. "
        "Paginated via limit (max 500) and offset."
    ),
    responses=merge_responses(AUTH_RESPONSES, FORBIDDEN_RESPONSE, NOT_FOUND_RESPONSE),
)
async def list_organization_ledger(
    organization_public_id: Annotated[UUID, _ORG_PUBLIC_ID],
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[LedgerEntryResponse]:
    organization = await get_organization_by_public_id(session, organization_public_id)
    if organization is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="organization not found")
    assert_ledger_read_access(ctx, organization)

    entries = await list_entries_for_organization(
        session,
        organization_id=organization.id,
        limit=limit,
        offset=offset,
    )

    reversed_ids = {
        entry.reverses_entry_id for entry in entries if entry.reverses_entry_id is not None
    }
    subscription_ids = {
        entry.subscription_id for entry in entries if entry.subscription_id is not None
    }
    reversed_public_by_id: dict[int, UUID] = {}
    subscription_public_by_id: dict[int, UUID] = {}
    if reversed_ids:
        result = await session.execute(
            select(LedgerEntry.id, LedgerEntry.public_id).where(LedgerEntry.id.in_(reversed_ids))
        )
        reversed_public_by_id = {row[0]: row[1] for row in result.all()}
    if subscription_ids:
        result = await session.execute(
            select(Subscription.id, Subscription.public_id).where(
                Subscription.id.in_(subscription_ids)
            )
        )
        subscription_public_by_id = {row[0]: row[1] for row in result.all()}

    return [
        _to_response(
            entry,
            organization_public_id=organization.public_id,
            subscription_public_id=(
                subscription_public_by_id.get(entry.subscription_id)
                if entry.subscription_id is not None
                else None
            ),
            reverses_entry_public_id=(
                reversed_public_by_id.get(entry.reverses_entry_id)
                if entry.reverses_entry_id is not None
                else None
            ),
        )
        for entry in entries
    ]


@router.get(
    "/ledger/{entry_public_id}",
    response_model=LedgerEntryResponse,
    summary="Get ledger entry",
    description=(
        "Returns a single ledger entry by public UUID, including reversal and subscription links. "
        "Requires platform_admin, revops_read, or a tenant key scoped to that organization."
    ),
    responses=merge_responses(AUTH_RESPONSES, FORBIDDEN_RESPONSE, NOT_FOUND_RESPONSE),
)
async def get_ledger_entry(
    entry_public_id: Annotated[UUID, _ENTRY_PUBLIC_ID],
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> LedgerEntryResponse:
    entry = await get_entry_by_public_id(session, entry_public_id)
    if entry is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ledger entry not found")

    organization = await _load_organization_by_id(session, entry.organization_id)
    if organization is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="organization not found")
    assert_ledger_read_access(ctx, organization)

    reverses_entry_public_id: UUID | None = None
    subscription_public_id: UUID | None = None
    if entry.reverses_entry_id is not None:
        result = await session.execute(
            select(LedgerEntry.public_id).where(LedgerEntry.id == entry.reverses_entry_id)
        )
        row = result.one_or_none()
        if row is not None:
            reverses_entry_public_id = row[0]
    if entry.subscription_id is not None:
        result = await session.execute(
            select(Subscription.public_id).where(Subscription.id == entry.subscription_id)
        )
        row = result.one_or_none()
        if row is not None:
            subscription_public_id = row[0]

    return _to_response(
        entry,
        organization_public_id=organization.public_id,
        subscription_public_id=subscription_public_id,
        reverses_entry_public_id=reverses_entry_public_id,
    )
