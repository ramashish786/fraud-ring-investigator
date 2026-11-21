"""Real PostgreSQL + unreachable Neo4j: readiness must say exactly that."""

import asyncio
from typing import Any

import httpx
import pytest

from app.core.container import Container
from app.main import create_app
from workers.main import Worker

pytestmark = pytest.mark.integration


async def test_readiness_with_real_postgres_and_neo4j_down(migrated_db: str, make_settings: Any) -> None:
    container = Container.build(make_settings(database_url=migrated_db))
    app = create_app(container=container)
    app.state.container = container
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            r = await c.get("/api/v1/health/ready")
        checks = {x["name"]: x for x in r.json()["checks"]}
        assert r.status_code == 503
        assert checks["postgres"]["status"] == "ok" and checks["postgres"]["latency_ms"] is not None
        assert checks["neo4j"]["status"] == "error"
    finally:
        await container.aclose()


async def test_worker_idle_loop_and_graceful_stop(migrated_db: str, make_settings: Any) -> None:
    from app.infrastructure.database.engine import Database

    settings = make_settings(database_url=migrated_db)
    container = Container(settings=settings, db=Database.from_settings(settings), neo4j=None)
    worker = Worker(container, runner=None, ingestion=None, poll_interval_s=0.05)  # type: ignore[arg-type]
    task = asyncio.create_task(worker.run())
    await asyncio.sleep(0.2)
    worker.stop()
    await asyncio.wait_for(task, timeout=2)  # stops promptly, no hang
    assert task.done() and task.exception() is None
    await container.aclose()
