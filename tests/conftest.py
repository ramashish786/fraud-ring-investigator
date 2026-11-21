"""Shared fixtures.

Unit tests need no services. Integration tests need PostgreSQL at TEST_DATABASE_URL
(default: local dev cluster) and are skipped with a clear reason if it is unreachable.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Callable
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.settings import Settings

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "postgresql+asyncpg://fri:fri@localhost:55432/fri_test")


@pytest.fixture
def make_settings() -> Callable[..., Settings]:
    def _make(**overrides: Any) -> Settings:
        base: dict[str, Any] = {
            "env": "test",
            "database_url": TEST_DATABASE_URL,
            "neo4j_password": "test-neo4j-pw",
            "jwt_secret": "test-jwt-secret-0123456789abcdef0123456789",
            "log_json": False,
            "neo4j_uri": "bolt://127.0.0.1:1",  # nothing listens here -> deterministic "down"
            "health_check_timeout_s": 1.0,
            "neo4j_connection_timeout_s": 0.5,
        }
        base.update(overrides)
        return Settings(_env_file=None, **base)  # type: ignore[call-arg]

    return _make


def _postgres_reachable() -> bool:
    async def probe() -> None:
        engine = create_async_engine(TEST_DATABASE_URL)
        try:
            async with engine.connect():
                pass
        finally:
            await engine.dispose()

    try:
        asyncio.run(asyncio.wait_for(probe(), 3))
        return True
    except Exception:
        return False


@pytest.fixture(scope="session")
def migrated_db() -> str:
    """Bring the test database to `head` once per session."""
    if not _postgres_reachable():
        pytest.skip(f"PostgreSQL not reachable at TEST_DATABASE_URL ({TEST_DATABASE_URL.split('@')[-1]})")
    from alembic import command
    from alembic.config import Config

    os.environ["DATABASE_URL"] = TEST_DATABASE_URL
    cfg = Config("alembic.ini")
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    return TEST_DATABASE_URL


@pytest.fixture
async def db_session(migrated_db: str) -> AsyncIterator[AsyncSession]:
    """A session inside a transaction that is always rolled back -> tests don't leak rows."""
    engine = create_async_engine(migrated_db)
    async with engine.connect() as conn:
        trans = await conn.begin()
        session = AsyncSession(bind=conn, expire_on_commit=False, join_transaction_mode="create_savepoint")
        try:
            yield session
        finally:
            await session.close()
            await trans.rollback()
    await engine.dispose()
