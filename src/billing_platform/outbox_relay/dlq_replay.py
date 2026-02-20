"""Idempotent replay of outbox dead letters back to publishable state (ADR-001)."""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import TextIO

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from billing_platform.domain.models.outbox_dead_letter import OutboxDeadLetter
from billing_platform.domain.models.outbox_message import OutboxMessage


class ReplayStatus(StrEnum):
    replayed = "replayed"
    already_replayed = "already_replayed"
    not_found = "not_found"
    outbox_missing = "outbox_missing"


@dataclass(frozen=True, slots=True)
class ReplayResult:
    dlq_id: int
    status: ReplayStatus
    outbox_message_id: int | None = None
    event_type: str | None = None
    replayed_at: str | None = None


async def replay_dead_letters(
    session_factory: async_sessionmaker[AsyncSession],
    dlq_ids: list[int],
    *,
    dry_run: bool = False,
) -> list[ReplayResult]:
    """Reset poisoned outbox rows for relay re-publish; mark DLQ rows replayed.

    Does not write to Kafka or ledger — relay picks up unpublished rows after commit.
    Replaying the same ``dlq_id`` twice is a no-op (``already_replayed``).
    """
    if not dlq_ids:
        return []

    unique_ids = list(dict.fromkeys(dlq_ids))
    results: list[ReplayResult] = []

    async with session_factory() as session, session.begin():
        rows_result = await session.execute(
            select(OutboxDeadLetter).where(OutboxDeadLetter.id.in_(unique_ids)).with_for_update()
        )
        rows_by_id = {row.id: row for row in rows_result.scalars().all()}

        for dlq_id in unique_ids:
            dlq = rows_by_id.get(dlq_id)
            if dlq is None:
                results.append(ReplayResult(dlq_id=dlq_id, status=ReplayStatus.not_found))
                continue

            if dlq.replayed_at is not None:
                results.append(
                    ReplayResult(
                        dlq_id=dlq_id,
                        status=ReplayStatus.already_replayed,
                        outbox_message_id=dlq.outbox_message_id,
                        event_type=dlq.event_type,
                        replayed_at=dlq.replayed_at.astimezone(UTC)
                        .isoformat()
                        .replace("+00:00", "Z"),
                    )
                )
                continue

            outbox = await session.get(OutboxMessage, dlq.outbox_message_id)
            if outbox is None:
                results.append(
                    ReplayResult(
                        dlq_id=dlq_id,
                        status=ReplayStatus.outbox_missing,
                        outbox_message_id=dlq.outbox_message_id,
                        event_type=dlq.event_type,
                    )
                )
                continue

            replay_ts = datetime.now(UTC)
            if not dry_run:
                outbox.published_at = None
                outbox.publish_attempts = 0
                outbox.last_error = None
                dlq.replayed_at = replay_ts

            results.append(
                ReplayResult(
                    dlq_id=dlq_id,
                    status=ReplayStatus.replayed,
                    outbox_message_id=outbox.id,
                    event_type=dlq.event_type,
                    replayed_at=replay_ts.isoformat().replace("+00:00", "Z"),
                )
            )

        if dry_run:
            await session.rollback()
        else:
            await session.flush()

    return results


def emit_audit_log(results: list[ReplayResult], *, stream: TextIO = sys.stdout) -> None:
    """Write one JSON object per replay result to stdout (ops audit)."""
    for result in results:
        print(json.dumps(asdict(result)), file=stream)
