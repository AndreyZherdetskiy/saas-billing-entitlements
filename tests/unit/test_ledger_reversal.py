"""Unit tests: ledger reversal keeps original entry (append-only)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from billing_platform.domain.models.ledger import LedgerEntryType
from billing_platform.domain.models.outbox_message import OutboxMessage
from billing_platform.services.ledger import get_entry, post, reverse
from billing_platform.services.organizations import create_organization


@pytest.fixture
async def posted_entry(db_session: AsyncSession):
    """Create organization and post a ledger entry."""
    org = await create_organization(
        db_session,
        name="Ledger Reversal Org",
        external_id=f"ext-ledger-{uuid.uuid4().hex[:8]}",
        idempotency_key=f"idem-org-ledger-{uuid.uuid4().hex[:8]}",
    )
    entry = await post(
        db_session,
        organization_id=org.id,
        entry_type=LedgerEntryType.invoice_paid.value,
        amount_cents=1000,
        currency="USD",
        idempotency_key=f"ledger-post-{uuid.uuid4().hex[:8]}",
        correlation_id="corr-post-1",
    )
    await db_session.flush()
    return org, entry


@pytest.mark.asyncio
async def test_reversal_does_not_delete_original(
    db_session: AsyncSession,
    posted_entry,
) -> None:
    org, posted = posted_entry
    rev = await reverse(
        db_session,
        entry_id=posted.id,
        idempotency_key="rev-1",
        correlation_id="c1",
    )
    assert rev.reverses_entry_id == posted.id
    assert rev.amount_cents == -posted.amount_cents
    assert await get_entry(db_session, posted.id) is not None

    outbox_result = await db_session.execute(
        select(OutboxMessage).where(OutboxMessage.event_type == "ledger.entry_posted")
    )
    outbox_rows = list(outbox_result.scalars().all())
    assert len(outbox_rows) == 2
    reversal_payload = next(
        row.payload for row in outbox_rows if row.payload.get("entry_type") == "reversal"
    )
    assert reversal_payload["entry_public_id"] == str(rev.public_id)
    assert reversal_payload["organization_public_id"] == str(org.public_id)
    assert "organization_id" not in reversal_payload
    assert "subscription_id" not in reversal_payload
    assert "invoice_id" not in reversal_payload
    assert "reverses_entry_id" not in reversal_payload
    assert reversal_payload["reverses_entry_public_id"] == str(posted.public_id)
