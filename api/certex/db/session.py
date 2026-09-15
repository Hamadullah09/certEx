"""Engine and session factories.

Two stacks coexist deliberately:

* The FastAPI process uses **asyncpg** through :func:`get_async_session`.
* Celery workers and Alembic use **psycopg** synchronously. Pipeline stages do
  blocking CPU work (OCR, rasterisation), so an event loop would buy nothing and
  would complicate ``prefork`` worker semantics.

Both read the same models and the same migrations.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from certex.config import Settings, get_settings

__all__ = [
    "async_session_scope",
    "dispose_engines",
    "get_async_engine",
    "get_async_session",
    "get_async_sessionmaker",
    "get_sync_engine",
    "get_sync_sessionmaker",
    "ping_database",
    "session_scope",
]

_STATEMENT_TIMEOUT_MS = 120_000


@lru_cache(maxsize=1)
def get_async_engine() -> AsyncEngine:
    settings: Settings = get_settings()
    return create_async_engine(
        settings.database_url,
        echo=settings.db_echo,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_recycle=settings.db_pool_recycle_seconds,
        pool_pre_ping=True,
        connect_args={
            "server_settings": {
                "application_name": "certex-api",
                "statement_timeout": str(_STATEMENT_TIMEOUT_MS),
            }
        },
    )


@lru_cache(maxsize=1)
def get_async_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        bind=get_async_engine(),
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )


@lru_cache(maxsize=1)
def get_sync_engine() -> Engine:
    """Engine for Celery workers.

    ``NullPool`` is intentional. A prefork worker forks after the engine is
    created; inherited sockets shared between processes corrupt the protocol.
    Opening a connection per task is cheap next to seconds of OCR.
    """
    settings: Settings = get_settings()
    engine = create_engine(
        settings.database_url_sync,
        echo=settings.db_echo,
        poolclass=NullPool,
        pool_pre_ping=True,
        connect_args={"application_name": "certex-worker"},
    )

    @event.listens_for(engine, "connect")
    def _set_statement_timeout(dbapi_connection: object, _record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        try:
            cursor.execute(f"SET statement_timeout = {_STATEMENT_TIMEOUT_MS}")
        finally:
            cursor.close()

    return engine


@lru_cache(maxsize=1)
def get_sync_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(bind=get_sync_engine(), expire_on_commit=False, autoflush=False)


async def get_async_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a session that commits on success.

    A handler that raises leaves the transaction rolled back, so a failed request
    can never half-write audit rows.
    """
    factory = get_async_sessionmaker()
    async with factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


@asynccontextmanager
async def async_session_scope() -> AsyncIterator[AsyncSession]:
    """Transactional scope for code outside the request cycle."""
    factory = get_async_sessionmaker()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope for synchronous worker code."""
    factory = get_sync_sessionmaker()
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


async def ping_database() -> bool:
    """Cheap liveness probe used by the health endpoint."""
    engine = get_async_engine()
    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))
    return True


async def dispose_engines() -> None:
    """Release pooled connections on shutdown."""
    await get_async_engine().dispose()
    get_sync_engine().dispose()
