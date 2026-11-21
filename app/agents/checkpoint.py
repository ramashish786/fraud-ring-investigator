"""LangGraph PostgreSQL checkpointer (spec §9, D-014).

Uses psycopg3 (LangGraph's requirement) with its own small pool, separate from the
SQLAlchemy/asyncpg pool. Its tables are created by `setup()` in the migrate step and are
excluded from Alembic (D-035).

    python -m app.agents.checkpoint      # create/upgrade checkpoint tables
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.core.settings import get_settings


def conninfo(database_url: str) -> str:
    return database_url.replace("postgresql+asyncpg://", "postgresql://", 1)


@asynccontextmanager
async def open_checkpointer(database_url: str, max_size: int = 5) -> AsyncIterator[AsyncPostgresSaver]:
    pool = AsyncConnectionPool(
        conninfo(database_url),
        min_size=1,
        max_size=max_size,
        open=False,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
    )
    await pool.open()
    try:
        yield AsyncPostgresSaver(pool)  # type: ignore[arg-type]
    finally:
        await pool.close()


async def setup_checkpointer(database_url: str) -> None:
    async with open_checkpointer(database_url, max_size=1) as saver:
        await saver.setup()


if __name__ == "__main__":
    asyncio.run(setup_checkpointer(get_settings().database_url.get_secret_value()))
