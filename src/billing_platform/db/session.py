"""Async database engines and session factories (primary + optional read replica).

Cyclic import with ``db.replica`` — ``get_read_session`` lazy-imports routing helpers.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from billing_platform.config import get_settings

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None
_read_engine: AsyncEngine | None = None
_read_session_factory: async_sessionmaker[AsyncSession] | None = None


def reset_db_singletons() -> None:
    """Clear cached engines/factories (tests only)."""
    global _engine, _session_factory, _read_engine, _read_session_factory
    _engine = None
    _session_factory = None
    _read_engine = None
    _read_session_factory = None


def get_engine() -> AsyncEngine:
    """Return a process-wide async engine for the primary (lazy singleton)."""
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(
            settings.database_url,
            pool_pre_ping=True,
            pool_size=settings.database_pool_size,
            max_overflow=settings.database_max_overflow,
        )
    return _engine


def get_read_engine() -> AsyncEngine:
    """Return a process-wide async engine for the read replica."""
    global _read_engine
    settings = get_settings()
    if settings.database_read_url is None:
        msg = "database_read_url is not configured"
        raise RuntimeError(msg)
    if _read_engine is None:
        _read_engine = create_async_engine(
            settings.database_read_url,
            pool_pre_ping=True,
            pool_size=settings.database_pool_size,
            max_overflow=settings.database_max_overflow,
        )
    return _read_engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return a process-wide async session factory for the primary."""
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            get_engine(),
            expire_on_commit=False,
            class_=AsyncSession,
        )
    return _session_factory


def get_read_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return a process-wide async session factory for the read replica."""
    global _read_session_factory
    if _read_session_factory is None:
        _read_session_factory = async_sessionmaker(
            get_read_engine(),
            expire_on_commit=False,
            class_=AsyncSession,
        )
    return _read_session_factory


async def get_session() -> AsyncIterator[AsyncSession]:
    """Yield an async DB session on the primary (FastAPI dependency)."""
    async with get_session_factory()() as session:
        yield session


async def get_read_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: replica when lag OK, else primary. No query parameters."""
    from billing_platform.db.replica import select_read_session_factory

    factory, _route = await select_read_session_factory(allow_stale=False)
    async with factory() as session:
        yield session


async def close_db_engine() -> None:
    """Dispose shared engines and session factories (application shutdown)."""
    global _engine, _session_factory, _read_engine, _read_session_factory
    if _read_engine is not None:
        await _read_engine.dispose()
        _read_engine = None
        _read_session_factory = None
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None
