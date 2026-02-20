from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from billing_platform.domain.models.organization import Organization
from billing_platform.services.api_keys import resolve_organization_by_public_id


async def _find_existing_organization(
    session: AsyncSession,
    *,
    idempotency_key: str,
    external_id: str,
) -> Organization | None:
    by_idem = await session.execute(
        select(Organization).where(Organization.idempotency_key == idempotency_key)
    )
    org = by_idem.scalar_one_or_none()
    if org is not None:
        return org

    by_external = await session.execute(
        select(Organization).where(Organization.external_id == external_id)
    )
    return by_external.scalar_one_or_none()


async def create_organization(
    session: AsyncSession,
    *,
    name: str,
    external_id: str,
    idempotency_key: str,
    billing_email: str | None = None,
    metadata: dict[str, object] | None = None,
    public_id: UUID | None = None,
) -> Organization:
    """Create an organization idempotently by idempotency_key and external_id.

    Pass ``public_id`` only for local deterministic bootstrap (demo seed).
    """
    org = await _find_existing_organization(
        session,
        idempotency_key=idempotency_key,
        external_id=external_id,
    )
    if org is not None:
        return org

    organization = Organization(
        name=name,
        external_id=external_id,
        idempotency_key=idempotency_key,
        billing_email=billing_email,
        metadata_=metadata or {},
    )
    if public_id is not None:
        organization.public_id = public_id
    session.add(organization)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        org = await _find_existing_organization(
            session,
            idempotency_key=idempotency_key,
            external_id=external_id,
        )
        if org is not None:
            return org
        raise
    return organization


async def get_organization_by_public_id(
    session: AsyncSession,
    public_id: object,
) -> Organization | None:
    """Return an organization by public_id or None if missing/deleted."""
    if not isinstance(public_id, UUID):
        public_id = UUID(str(public_id))
    return await resolve_organization_by_public_id(session, public_id)


async def update_organization_metadata(
    session: AsyncSession,
    organization: Organization,
    *,
    name: str | None = None,
    billing_email: str | None = None,
    metadata: dict[str, object] | None = None,
) -> Organization:
    """Patch organization fields."""
    if name is not None:
        organization.name = name
    if billing_email is not None:
        organization.billing_email = billing_email
    if metadata is not None:
        organization.metadata_ = metadata
    await session.flush()
    return organization
