"""Transactional outbox enqueue hooks — thin wrapper over OutboxService."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from billing_platform.services.outbox import OutboxService


def _organization_id_from_partition_key(partition_key: str) -> int:
    """Kafka partition_key is usually org BIGINT; recon uses run UUID — then 0."""
    try:
        return int(partition_key)
    except ValueError:
        return 0


async def enqueue_outbox(
    session: AsyncSession,
    *,
    aggregate_type: str,
    aggregate_id: str,
    event_type: str,
    payload: dict[str, object],
    idempotency_key: str,
    partition_key: str,
) -> None:
    """Insert an outbox row idempotently (ON CONFLICT DO NOTHING)."""
    await OutboxService.enqueue(
        session,
        event_type=event_type,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        organization_id=_organization_id_from_partition_key(partition_key),
        partition_key=partition_key,
        payload=payload,
        idempotency_key=idempotency_key,
    )
