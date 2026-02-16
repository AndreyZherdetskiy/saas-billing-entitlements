"""Read-replica lag probing and routing helpers.

Cyclic import with ``db.session`` — lazy imports in function bodies (see ``session.py``).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

ReplicaLagProvider = Callable[[], Awaitable[float | None]]

_lag_provider: ReplicaLagProvider | None = None

_REPLICA_LAG_SQL = text(
    """
    SELECT EXTRACT(
        EPOCH FROM (
            now() AT TIME ZONE 'utc'
            - COALESCE(pg_last_xact_replay_timestamp(), now() AT TIME ZONE 'utc')
        )
    ) AS lag_seconds
    WHERE pg_is_in_recovery()
    """
)


def should_use_replica(*, lag_seconds: float | None, threshold: float) -> bool:
    """Return True when replica lag is known and strictly below the threshold."""
    if lag_seconds is None:
        return False
    return lag_seconds < threshold


def set_replica_lag_provider(provider: ReplicaLagProvider | None) -> None:
    """Override lag measurement (tests) or reset with None."""
    global _lag_provider
    _lag_provider = provider


def reset_replica_lag_provider() -> None:
    """Clear any test override for lag measurement."""
    set_replica_lag_provider(None)


async def measure_replica_lag_seconds(session: AsyncSession) -> float | None:
    """Query replica replay lag in seconds; None when not on a standby."""
    result = await session.execute(_REPLICA_LAG_SQL)
    row = result.one_or_none()
    if row is None:
        return None
    lag = row.lag_seconds
    if lag is None:
        return None
    return float(lag)


async def get_replica_lag_seconds() -> float | None:
    """Return replica lag via injectable provider or a live replica query."""
    if _lag_provider is not None:
        return await _lag_provider()

    from billing_platform.config import get_settings
    from billing_platform.db.session import get_read_session_factory

    settings = get_settings()
    if settings.database_read_url is None:
        return None

    factory = get_read_session_factory()
    async with factory() as session:
        try:
            return await measure_replica_lag_seconds(session)
        except Exception:
            return None


async def select_read_session_factory(
    *,
    allow_stale: bool = False,
) -> tuple[async_sessionmaker[AsyncSession], str]:
    """Pick primary or read session factory based on lag and settings.

    Returns (session_factory, route_label) where route_label is ``"replica"`` or
    ``"primary"``.
    """
    from billing_platform.config import get_settings
    from billing_platform.db.session import get_read_session_factory, get_session_factory

    settings = get_settings()
    primary_factory = get_session_factory()

    if settings.database_read_url is None:
        return primary_factory, "primary"

    if allow_stale:
        return get_read_session_factory(), "replica"

    lag = await get_replica_lag_seconds()
    if should_use_replica(
        lag_seconds=lag,
        threshold=float(settings.replica_lag_threshold_seconds),
    ):
        return get_read_session_factory(), "replica"

    return primary_factory, "primary"
