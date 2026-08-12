"""Async MySQL engine and session factory construction."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from rag_api.config import Settings


def create_database_engine(settings: Settings) -> AsyncEngine:
    """Create a pooled async engine without opening a connection eagerly."""

    return create_async_engine(
        settings.mysql_dsn.get_secret_value(),
        pool_pre_ping=True,
        pool_recycle=1800,
    )


def create_session_factory(
    engine: AsyncEngine,
) -> async_sessionmaker[AsyncSession]:
    """Create request-scoped sessions that never expire loaded attributes."""

    return async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
