"""Neo4j async driver lifecycle.

* One driver per process (it owns a connection pool); created at startup, closed at shutdown.
* Creating the driver does not connect, so the API can start while Neo4j is still booting
  and simply report "not ready" until it is reachable.
* Application reads go through `read()`: a managed *read* transaction (the server rejects
  writes inside it) with a per-query timeout. This is the only path the query catalog uses (D-008).
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any, LiteralString

from neo4j import AsyncDriver, AsyncGraphDatabase, AsyncManagedTransaction, unit_of_work

from app.core.settings import Settings


class Neo4jClient:
    def __init__(self, driver: AsyncDriver, database: str, query_timeout_s: float) -> None:
        self._driver = driver
        self._database = database
        self._timeout = query_timeout_s

    @classmethod
    def from_settings(cls, s: Settings) -> Neo4jClient:
        driver = AsyncGraphDatabase.driver(
            s.neo4j_uri,
            auth=(s.neo4j_user, s.neo4j_password.get_secret_value()),
            max_connection_pool_size=s.neo4j_max_pool_size,
            connection_timeout=s.neo4j_connection_timeout_s,
            connection_acquisition_timeout=s.neo4j_connection_timeout_s,
        )
        return cls(driver, s.neo4j_database, s.neo4j_query_timeout_s)

    async def read(
        self, cypher: LiteralString, params: Mapping[str, Any] | None = None, timeout_s: float | None = None
    ) -> list[dict[str, Any]]:
        # timeout is enforced server-side for the whole transaction function
        @unit_of_work(timeout=timeout_s or self._timeout)
        async def work(tx: AsyncManagedTransaction) -> list[dict[str, Any]]:
            result = await tx.run(cypher, dict(params or {}))
            return [record.data() async for record in result]

        async with self._driver.session(database=self._database) as session:
            return await session.execute_read(work)

    async def write(self, cypher: LiteralString, params: Mapping[str, Any] | None = None) -> None:
        """Writes are for loaders/bootstrap and the Investigation mirror only, never for LLM-driven paths."""

        @unit_of_work(timeout=self._timeout)
        async def work(tx: AsyncManagedTransaction) -> None:
            await (await tx.run(cypher, dict(params or {}))).consume()

        async with self._driver.session(database=self._database) as session:
            await session.execute_write(work)

    async def run_schema(self, statement: LiteralString) -> None:
        """Schema DDL must run in an auto-commit transaction (not allowed in managed tx)."""
        async with self._driver.session(database=self._database) as session:
            await (await session.run(statement)).consume()

    async def ping(self, timeout_s: float) -> float:
        start = time.perf_counter()
        rows = await self.read("RETURN 1 AS ok", timeout_s=timeout_s)
        if rows != [{"ok": 1}]:
            raise RuntimeError("unexpected ping result")
        return (time.perf_counter() - start) * 1000

    async def close(self) -> None:
        await self._driver.close()
