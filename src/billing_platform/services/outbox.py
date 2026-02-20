"""Transactional outbox service (ADR-001)."""

from __future__ import annotations

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from billing_platform.domain.models.outbox_message import OutboxMessage

_FORBIDDEN_PAYLOAD_KEYS = frozenset({"organization_id", "subscription_id"})


def _sanitize_payload(payload: dict[str, object]) -> dict[str, object]:
    """Strip internal BIGINT ids from Kafka-bound outbox payloads (dual-id policy)."""
    return {key: value for key, value in payload.items() if key not in _FORBIDDEN_PAYLOAD_KEYS}


class OutboxService:
    """Enqueue domain facts in the same DB transaction as business writes."""

    @staticmethod
    async def enqueue(
        session: AsyncSession,
        *,
        event_type: str,
        aggregate_type: str,
        aggregate_id: str,
        organization_id: int,
        partition_key: str,
        payload: dict[str, object],
        idempotency_key: str,
    ) -> int:
        """Insert an outbox row idempotently; return id or 0 on conflict."""
        body = _sanitize_payload(payload)

        stmt = (
            insert(OutboxMessage)
            .values(
                aggregate_type=aggregate_type,
                aggregate_id=aggregate_id,
                event_type=event_type,
                payload=body,
                idempotency_key=idempotency_key,
                partition_key=partition_key,
            )
            .on_conflict_do_nothing(index_elements=["idempotency_key"])
            .returning(OutboxMessage.id)
        )
        result = await session.execute(stmt)
        outbox_id = result.scalar_one_or_none()
        return int(outbox_id) if outbox_id is not None else 0


async def enqueue(
    session: AsyncSession,
    *,
    event_type: str,
    aggregate_type: str,
    aggregate_id: str,
    organization_id: int,
    partition_key: str,
    payload: dict[str, object],
    idempotency_key: str,
) -> int:
    """Module-level alias for OutboxService.enqueue."""
    return await OutboxService.enqueue(
        session,
        event_type=event_type,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        organization_id=organization_id,
        partition_key=partition_key,
        payload=payload,
        idempotency_key=idempotency_key,
    )
