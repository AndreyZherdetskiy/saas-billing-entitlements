"""Outbox relay: poll unpublished rows and publish to Kafka (ADR-001/002/004)."""

from __future__ import annotations

import json
import uuid
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any

from aiokafka import AIOKafkaProducer
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from billing_platform.config import Settings, get_settings
from billing_platform.domain.models.organization import Organization
from billing_platform.domain.models.outbox_dead_letter import OutboxDeadLetter
from billing_platform.domain.models.outbox_message import OutboxMessage
from billing_platform.events.schemas.v1.envelope import EventEnvelope
from billing_platform.observability.metrics import (
    record_outbox_lag_seconds,
    record_outbox_unpublished_count,
)

SCHEMA_VERSION = 1
DLQ_TOPIC = "billing.dlq"

SUBSCRIPTION_EVENT_TYPES = frozenset(
    {
        "subscription.trial_started",
        "subscription.activated",
        "subscription.payment_failed",
        "subscription.past_due",
        "subscription.canceled",
        "subscription.plan_changed",
    }
)


def resolve_topic(event_type: str) -> str:
    if event_type.startswith("subscription."):
        return "billing.subscription.events"
    if event_type.startswith("invoice."):
        return "billing.invoice.events"
    if event_type.startswith("ledger."):
        return "billing.ledger.events"
    if event_type.startswith("reconciliation."):
        return "billing.reconciliation.events"
    if event_type.startswith("entitlement.") or event_type.startswith("subscription.access_"):
        return "billing.entitlement.events"
    return DLQ_TOPIC


def stable_event_id(outbox_id: int) -> str:
    """Deterministic event_id for at-least-once consumer dedup."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"billing-platform:outbox:{outbox_id}"))


def extract_correlation_id(payload: dict[str, Any], outbox_id: int) -> str:
    webhook_id = payload.get("webhook_id")
    if isinstance(webhook_id, str) and webhook_id:
        return webhook_id
    return str(outbox_id)


def build_envelope(
    row: OutboxMessage,
    *,
    organization_public_id: str,
) -> EventEnvelope:
    payload = dict(row.payload)
    return EventEnvelope(
        schema_version=SCHEMA_VERSION,
        event_id=stable_event_id(row.id),
        event_type=row.event_type,
        occurred_at=row.created_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        organization_id=organization_public_id,
        correlation_id=extract_correlation_id(payload, row.id),
        payload=payload,
    )


async def _load_org_public_ids(
    session: AsyncSession,
    partition_keys: set[str],
) -> dict[str, str]:
    org_ids = [int(key) for key in partition_keys if key.isdigit()]
    if not org_ids:
        return {}
    result = await session.execute(
        select(Organization.id, Organization.public_id).where(Organization.id.in_(org_ids))
    )
    return {str(org_id): str(public_id) for org_id, public_id in result.all()}


async def _move_to_dead_letter(
    session: AsyncSession,
    row: OutboxMessage,
    *,
    last_error: str | None,
) -> None:
    session.add(
        OutboxDeadLetter(
            outbox_message_id=row.id,
            aggregate_type=row.aggregate_type,
            aggregate_id=row.aggregate_id,
            event_type=row.event_type,
            payload=row.payload,
            partition_key=row.partition_key,
            publish_attempts=row.publish_attempts,
            last_error=last_error,
        )
    )
    row.published_at = datetime.now(UTC)


async def _publish_envelope(
    producer: AIOKafkaProducer,
    *,
    topic: str,
    outbox_id: int,
    partition_key: str,
    envelope: EventEnvelope,
) -> None:
    body = json.dumps(envelope.to_dict()).encode("utf-8")
    headers = [
        ("event_type", envelope.event_type.encode("utf-8")),
        ("schema_version", str(envelope.schema_version).encode("utf-8")),
        ("partition_key", partition_key.encode("utf-8")),
    ]
    await producer.send_and_wait(
        topic,
        value=body,
        key=str(outbox_id).encode("utf-8"),
        headers=headers,
    )


async def observe_outbox_backlog(session: AsyncSession) -> None:
    """Publish absolute unpublished depth and oldest-message lag gauges."""
    unpublished = int(
        (
            await session.execute(
                select(func.count())
                .select_from(OutboxMessage)
                .where(OutboxMessage.published_at.is_(None))
            )
        ).scalar_one()
    )
    oldest_at = (
        await session.execute(
            select(func.min(OutboxMessage.created_at)).where(OutboxMessage.published_at.is_(None))
        )
    ).scalar_one_or_none()
    lag_seconds = 0.0
    if oldest_at is not None:
        created = oldest_at if oldest_at.tzinfo is not None else oldest_at.replace(tzinfo=UTC)
        lag_seconds = max(0.0, (datetime.now(UTC) - created.astimezone(UTC)).total_seconds())
    record_outbox_unpublished_count(unpublished)
    record_outbox_lag_seconds(lag_seconds)


async def claim_outbox_batch(
    session: AsyncSession,
    *,
    limit: int,
    max_attempts: int,
) -> list[OutboxMessage]:
    """Lock unpublished outbox rows for relay publish (SKIP LOCKED for HA replicas)."""
    result = await session.execute(
        select(OutboxMessage)
        .where(
            OutboxMessage.published_at.is_(None),
            OutboxMessage.publish_attempts < max_attempts,
        )
        .order_by(OutboxMessage.created_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    return list(result.scalars().all())


async def poll_and_publish(
    batch_size: int | None = None,
    *,
    settings: Settings | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    producer: AIOKafkaProducer | None = None,
    own_producer: bool = False,
) -> int:
    """Poll pending outbox rows and publish envelopes to Kafka.

    Returns the number of successfully published messages.
    """
    cfg = settings or get_settings()
    limit = batch_size if batch_size is not None else cfg.outbox_batch_size
    max_attempts = cfg.outbox_max_attempts

    engine = None
    if session_factory is None:
        engine = create_async_engine(cfg.database_url, pool_pre_ping=True)
        session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    kafka_producer = producer
    if kafka_producer is None:
        own_producer = True
        kafka_producer = AIOKafkaProducer(
            bootstrap_servers=cfg.kafka_bootstrap_servers,
            acks="all",
        )
        await kafka_producer.start()

    published = 0
    try:
        async with session_factory() as session, session.begin():
            await observe_outbox_backlog(session)
            rows = await claim_outbox_batch(
                session,
                limit=limit,
                max_attempts=max_attempts,
            )
            if not rows:
                return 0

            org_public_ids = await _load_org_public_ids(
                session,
                {row.partition_key for row in rows},
            )

            for row in rows:
                org_public_id = org_public_ids.get(row.partition_key)
                if org_public_id is None:
                    row.publish_attempts += 1
                    row.last_error = (
                        f"organization not found for partition_key={row.partition_key}"
                    )
                    if row.publish_attempts >= max_attempts:
                        envelope = build_envelope(
                            row,
                            organization_public_id="unknown",
                        )
                        await _move_to_dead_letter(session, row, last_error=row.last_error)
                        with suppress(Exception):
                            await _publish_envelope(
                                kafka_producer,
                                topic=DLQ_TOPIC,
                                outbox_id=row.id,
                                partition_key=row.partition_key,
                                envelope=envelope,
                            )
                    continue

                envelope = build_envelope(row, organization_public_id=org_public_id)
                topic = resolve_topic(row.event_type)

                try:
                    await _publish_envelope(
                        kafka_producer,
                        topic=topic,
                        outbox_id=row.id,
                        partition_key=row.partition_key,
                        envelope=envelope,
                    )
                except Exception as exc:  # noqa: BLE001 — relay poison handling
                    row.publish_attempts += 1
                    row.last_error = str(exc)
                    if row.publish_attempts >= max_attempts:
                        await _move_to_dead_letter(session, row, last_error=row.last_error)
                        with suppress(Exception):
                            await _publish_envelope(
                                kafka_producer,
                                topic=DLQ_TOPIC,
                                outbox_id=row.id,
                                partition_key=row.partition_key,
                                envelope=envelope,
                            )
                    continue

                row.published_at = datetime.now(UTC)
                published += 1
    finally:
        if own_producer and kafka_producer is not None:
            await kafka_producer.stop()
        if engine is not None:
            await engine.dispose()

    return published
