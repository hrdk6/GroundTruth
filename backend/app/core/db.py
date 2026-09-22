"""Database engines and sessions.

Two engines on one URL, deliberately:

* **async** for the FastAPI request path, so a slow pgvector scan doesn't block
  the event loop while other requests wait.
* **sync** for ingestion, migrations, and the eval runner, which are batch jobs
  where async buys nothing and costs readability.

Both use psycopg 3 (`postgresql+psycopg://`), which speaks either protocol, so
there is only one driver and one connection string to keep straight.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from functools import lru_cache
from typing import Any

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session, sessionmaker

from app.core.settings import get_settings

# Without this, psycopg retries every resolved address until the OS gives up,
# which turns "Postgres isn't running" into a multi-minute hang on /health.
CONNECT_TIMEOUT_SECONDS = 3


def _async_url(url: str) -> str:
    """psycopg 3 uses the same scheme for both; normalize older psycopg2 URLs."""
    return url.replace("postgresql+psycopg2://", "postgresql+psycopg://")


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """Synchronous engine for CLIs, ingestion, and evals."""
    settings = get_settings()
    return create_engine(
        settings.database_url,
        pool_pre_ping=True,  # survives Postgres restarts during local dev
        connect_args={"connect_timeout": CONNECT_TIMEOUT_SECONDS},
        future=True,
    )


@lru_cache(maxsize=1)
def get_async_engine() -> AsyncEngine:
    """Asynchronous engine for the API request path."""
    settings = get_settings()
    return create_async_engine(
        _async_url(settings.database_url),
        pool_pre_ping=True,
        connect_args={"connect_timeout": CONNECT_TIMEOUT_SECONDS},
        future=True,
    )


@lru_cache(maxsize=1)
def _session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)


@lru_cache(maxsize=1)
def _async_session_factory() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(bind=get_async_engine(), expire_on_commit=False)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope for synchronous code: commit on success, roll back on error."""
    session = _session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding an async session."""
    async with _async_session_factory()() as session:
        yield session


async def check_connection() -> dict[str, Any]:
    """Health probe: is Postgres reachable, and is pgvector installed?

    pgvector is reported separately because a database that is *up* but missing
    the extension fails later in a much more confusing way.
    """
    engine = get_async_engine()
    async with engine.connect() as conn:
        version = (await conn.execute(text("SHOW server_version"))).scalar_one()
        has_vector = (
            await conn.execute(
                text("SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector')")
            )
        ).scalar_one()
    return {"server_version": version, "pgvector": bool(has_vector)}


def reset_engines() -> None:
    """Drop cached engines. Used by tests that point at a different database."""
    get_engine.cache_clear()
    get_async_engine.cache_clear()
    _session_factory.cache_clear()
    _async_session_factory.cache_clear()
