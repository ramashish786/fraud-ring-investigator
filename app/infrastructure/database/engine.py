"""Async SQLAlchemy engine, session factory and unit of work.

Transaction rule: one unit of work = one database transaction. Services open it,
repositories use the session it yields, and it commits on success or rolls back on
any exception. Repositories never commit on their own, so a service can combine
several writes (e.g. investigation + status history + job + audit) atomically.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.core.settings import Settings


class Database:
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine
        self.sessionmaker = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)

    @classmethod
    def from_settings(cls, s: Settings) -> Database:
        engine = create_async_engine(
            s.database_url.get_secret_value(),
            pool_size=s.db_pool_size,
            max_overflow=s.db_max_overflow,
            pool_timeout=s.db_pool_timeout_s,
            pool_pre_ping=True,  # drop dead connections after DB restarts
            pool_recycle=1800,
            connect_args={
                "server_settings": {"application_name": s.app_name, "statement_timeout": str(s.db_statement_timeout_ms)}
            },
        )
        return cls(engine)

    @asynccontextmanager
    async def unit_of_work(self) -> AsyncIterator[AsyncSession]:
        async with self.sessionmaker() as session, session.begin():
            yield session  # session.begin() commits on exit, rolls back on exception

    @asynccontextmanager
    async def read_session(self) -> AsyncIterator[AsyncSession]:
        async with self.sessionmaker() as session:
            yield session

    async def ping(self, timeout_s: float) -> float:
        """Round-trip `SELECT 1`; returns latency in ms. Raises on failure or timeout."""
        start = time.perf_counter()
        async with asyncio.timeout(timeout_s), self.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return (time.perf_counter() - start) * 1000

    async def dispose(self) -> None:
        await self.engine.dispose()
