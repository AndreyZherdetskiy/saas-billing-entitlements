"""Purge prod-like seed slice (--purge-prod-like-prefix)."""

from __future__ import annotations

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from billing_platform.domain.models.api_key import ApiKey
from billing_platform.domain.models.dunning import DunningAttempt, DunningCampaign
from billing_platform.domain.models.invoice import Invoice, InvoiceLineItem
from billing_platform.domain.models.ledger import LedgerEntry
from billing_platform.domain.models.organization import Organization
from billing_platform.domain.models.outbox_message import OutboxMessage
from billing_platform.domain.models.subscription import Subscription
from billing_platform.domain.models.usage_aggregate import UsageAggregate
from billing_platform.domain.models.usage_event import UsageEvent
from billing_platform.domain.models.webhook_event import WebhookEvent


def _prod_like_org_predicate():
    return or_(
        Organization.idempotency_key.like("pl_%"),
        Organization.metadata_["seed_slice"].as_string() == "prod_like",
    )


async def _collect_prod_like_org_ids(session: AsyncSession) -> list[int]:
    result = await session.execute(select(Organization.id).where(_prod_like_org_predicate()))
    return list(result.scalars().all())


async def purge_prod_like_prefix(session: AsyncSession) -> int:
    """Delete prod-like tagged rows; never touches global catalog or demo org."""
    org_ids = await _collect_prod_like_org_ids(session)
    if not org_ids:
        return 0

    sub_public_ids_result = await session.execute(
        select(Subscription.public_id).where(Subscription.organization_id.in_(org_ids))
    )
    sub_public_ids = [str(pid) for pid in sub_public_ids_result.scalars().all()]
    org_public_ids_result = await session.execute(
        select(Organization.public_id).where(Organization.id.in_(org_ids))
    )
    org_public_ids = [str(pid) for pid in org_public_ids_result.scalars().all()]
    aggregate_ids = sub_public_ids + org_public_ids

    await session.execute(
        delete(UsageEvent).where(
            or_(
                UsageEvent.idempotency_key.like("pl_usage_%"),
                UsageEvent.organization_id.in_(org_ids),
            )
        )
    )
    await session.execute(
        delete(UsageAggregate).where(UsageAggregate.organization_id.in_(org_ids))
    )
    await session.execute(
        delete(WebhookEvent).where(WebhookEvent.provider_event_id.like("evt_pl_%"))
    )
    if aggregate_ids:
        await session.execute(
            delete(OutboxMessage).where(OutboxMessage.aggregate_id.in_(aggregate_ids))
        )
    await session.execute(
        delete(LedgerEntry).where(
            or_(
                LedgerEntry.idempotency_key.like("pl_%"),
                LedgerEntry.organization_id.in_(org_ids),
            )
        )
    )
    invoice_ids_result = await session.execute(
        select(Invoice.id).where(
            or_(
                Invoice.idempotency_key.like("pl_period_%"),
                Invoice.organization_id.in_(org_ids),
            )
        )
    )
    invoice_ids = list(invoice_ids_result.scalars().all())
    if invoice_ids:
        await session.execute(
            delete(InvoiceLineItem).where(InvoiceLineItem.invoice_id.in_(invoice_ids))
        )
        await session.execute(delete(Invoice).where(Invoice.id.in_(invoice_ids)))

    await session.execute(
        delete(DunningAttempt).where(
            DunningAttempt.campaign_id.in_(
                select(DunningCampaign.id).where(DunningCampaign.organization_id.in_(org_ids))
            )
        )
    )
    await session.execute(
        delete(DunningCampaign).where(DunningCampaign.organization_id.in_(org_ids))
    )

    await session.execute(
        delete(ApiKey).where(
            ApiKey.organization_id.in_(org_ids),
        )
    )
    await session.execute(
        delete(Subscription).where(
            or_(
                Subscription.idempotency_key.like("sub_pl_%"),
                Subscription.organization_id.in_(org_ids),
            )
        )
    )
    await session.execute(delete(Organization).where(Organization.id.in_(org_ids)))
    await session.flush()
    return len(org_ids)
