"""Monthly PostgreSQL partitions for usage_events (ADR-011)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_USAGE_PARTITION_LOCK_NAMESPACE = 1_431_520_071


def month_bounds(dt: datetime) -> tuple[datetime, datetime]:
    """Return the UTC half-open month containing ``dt``."""
    if dt.tzinfo is None:
        raise ValueError("dt must be timezone-aware")

    start = dt.astimezone(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1)
    else:
        end = start.replace(month=start.month + 1)
    return start, end


async def ensure_usage_partition(
    session: AsyncSession,
    *,
    year: int,
    month: int,
) -> str:
    """Create one monthly usage-events partition and its local unique index."""
    start, end = month_bounds(datetime(year, month, 1, tzinfo=UTC))
    partition_name = f"usage_events_{year:04d}_{month:02d}"

    await session.execute(
        text(
            """
            SELECT pg_advisory_xact_lock(
                CAST(:lock_namespace AS integer),
                CAST(:month_key AS integer)
            )
            """
        ),
        {
            "lock_namespace": _USAGE_PARTITION_LOCK_NAMESPACE,
            "month_key": year * 100 + month,
        },
    )
    await session.execute(
        text(
            f"""
            CREATE TABLE IF NOT EXISTS {partition_name}
            PARTITION OF usage_events
            FOR VALUES FROM ('{start.isoformat()}') TO ('{end.isoformat()}')
            """
        )
    )
    await session.execute(
        text(
            f"""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_{partition_name}_org_idempotency
            ON {partition_name} (organization_id, idempotency_key)
            """
        )
    )
    return partition_name


async def ensure_current_and_next_partitions(session: AsyncSession) -> list[str]:
    """Ensure partitions for the current and next calendar months."""
    now = datetime.now(UTC)
    current_start, next_start = month_bounds(now)
    partition_names: list[str] = []
    for target in (current_start, next_start):
        partition_names.append(
            await ensure_usage_partition(
                session,
                year=target.year,
                month=target.month,
            )
        )
    return partition_names
