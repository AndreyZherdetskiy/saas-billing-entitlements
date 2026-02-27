"""Unit tests for read-replica lag routing."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from billing_platform.db.replica import (
    reset_replica_lag_provider,
    set_replica_lag_provider,
    should_use_replica,
)
from billing_platform.db.session import get_read_session, reset_db_singletons


class TestShouldUseReplica:
    def test_low_lag_returns_true(self) -> None:
        assert should_use_replica(lag_seconds=5.0, threshold=30.0) is True

    def test_lag_at_threshold_returns_false(self) -> None:
        assert should_use_replica(lag_seconds=30.0, threshold=30.0) is False

    def test_high_lag_returns_false(self) -> None:
        assert should_use_replica(lag_seconds=45.0, threshold=30.0) is False

    def test_missing_lag_returns_false(self) -> None:
        assert should_use_replica(lag_seconds=None, threshold=30.0) is False


@pytest.fixture(autouse=True)
def _clean_db_state() -> None:
    reset_db_singletons()
    reset_replica_lag_provider()
    yield
    reset_db_singletons()
    reset_replica_lag_provider()


@pytest.mark.asyncio
async def test_get_read_session_uses_primary_when_lag_high() -> None:
    primary_factory = MagicMock()
    primary_session = AsyncMock(spec=AsyncSession)
    primary_ctx = AsyncMock()
    primary_ctx.__aenter__ = AsyncMock(return_value=primary_session)
    primary_ctx.__aexit__ = AsyncMock(return_value=None)
    primary_factory.return_value = primary_ctx

    read_factory = MagicMock()

    async def lag_high() -> float:
        return 60.0

    set_replica_lag_provider(lag_high)

    with (
        patch("billing_platform.config.get_settings") as mock_settings,
        patch(
            "billing_platform.db.session.get_session_factory",
            return_value=primary_factory,
        ),
        patch(
            "billing_platform.db.session.get_read_session_factory",
            return_value=read_factory,
        ),
    ):
        mock_settings.return_value.database_read_url = "postgresql+asyncpg://ro/test"
        mock_settings.return_value.replica_lag_threshold_seconds = 30

        gen = get_read_session()
        session = await anext(gen)
        assert session is primary_session
        read_factory.assert_not_called()
        await gen.aclose()


@pytest.mark.asyncio
async def test_get_read_session_uses_replica_when_lag_low() -> None:
    primary_factory = MagicMock()
    read_factory = MagicMock()
    read_session = AsyncMock(spec=AsyncSession)
    read_ctx = AsyncMock()
    read_ctx.__aenter__ = AsyncMock(return_value=read_session)
    read_ctx.__aexit__ = AsyncMock(return_value=None)
    read_factory.return_value = read_ctx

    async def lag_low() -> float:
        return 2.0

    set_replica_lag_provider(lag_low)

    with (
        patch("billing_platform.config.get_settings") as mock_settings,
        patch(
            "billing_platform.db.session.get_session_factory",
            return_value=primary_factory,
        ),
        patch(
            "billing_platform.db.session.get_read_session_factory",
            return_value=read_factory,
        ),
    ):
        mock_settings.return_value.database_read_url = "postgresql+asyncpg://ro/test"
        mock_settings.return_value.replica_lag_threshold_seconds = 30

        gen = get_read_session()
        session = await anext(gen)
        assert session is read_session
        read_factory.assert_called_once()
        primary_factory.assert_not_called()
        await gen.aclose()


@pytest.mark.asyncio
async def test_get_read_session_primary_when_no_read_url() -> None:
    primary_factory = MagicMock()
    primary_session = AsyncMock(spec=AsyncSession)
    primary_ctx = AsyncMock()
    primary_ctx.__aenter__ = AsyncMock(return_value=primary_session)
    primary_ctx.__aexit__ = AsyncMock(return_value=None)
    primary_factory.return_value = primary_ctx
    read_factory = MagicMock()

    async def lag_low() -> float:
        return 1.0

    set_replica_lag_provider(lag_low)

    with (
        patch("billing_platform.config.get_settings") as mock_settings,
        patch(
            "billing_platform.db.session.get_session_factory",
            return_value=primary_factory,
        ),
        patch(
            "billing_platform.db.session.get_read_session_factory",
            return_value=read_factory,
        ),
    ):
        mock_settings.return_value.database_read_url = None
        mock_settings.return_value.replica_lag_threshold_seconds = 30

        gen = get_read_session()
        session = await anext(gen)
        assert session is primary_session
        read_factory.assert_not_called()
        await gen.aclose()
