"""API tests with injected fake dependencies (no network)."""

from collections.abc import AsyncIterator
from typing import Any, cast

import httpx
import pytest

from app.core.container import Container
from app.main import create_app


class FakeDep:
    def __init__(self, ok: bool) -> None:
        self.ok = ok

    async def ping(self, timeout_s: float) -> float:
        if not self.ok:
            raise OSError("down")
        return 2.0

    async def close(self) -> None: ...
    async def dispose(self) -> None: ...


def _client(make_settings: Any, pg_ok: bool, neo_ok: bool) -> httpx.AsyncClient:
    settings = make_settings()
    container = Container(settings=settings, db=cast(Any, FakeDep(pg_ok)), neo4j=cast(Any, FakeDep(neo_ok)))
    app = create_app(container=container)
    app.state.container = container  # ASGITransport doesn't run lifespan
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.fixture
async def healthy(make_settings: Any) -> AsyncIterator[httpx.AsyncClient]:
    async with _client(make_settings, True, True) as c:
        yield c


async def test_liveness(healthy: httpx.AsyncClient) -> None:
    r = await healthy.get("/api/v1/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


async def test_ready_when_all_dependencies_up(healthy: httpx.AsyncClient) -> None:
    r = await healthy.get("/api/v1/health/ready")
    assert r.status_code == 200
    assert {c["name"]: c["status"] for c in r.json()["checks"]} == {"postgres": "ok", "neo4j": "ok"}


async def test_not_ready_returns_503_with_failing_dependency(make_settings: Any) -> None:
    async with _client(make_settings, True, False) as c:
        r = await c.get("/api/v1/health/ready")
    assert r.status_code == 503
    body = r.json()
    assert body["status"] == "not_ready"
    assert {x["name"]: x["error"] for x in body["checks"]} == {"postgres": None, "neo4j": "OSError"}


async def test_request_id_is_propagated_or_generated(healthy: httpx.AsyncClient) -> None:
    r = await healthy.get("/api/v1/health", headers={"X-Request-ID": "abc12345-req"})
    assert r.headers["x-request-id"] == "abc12345-req"
    r = await healthy.get("/api/v1/health", headers={"X-Request-ID": "bad id\n<script>"})
    assert r.headers["x-request-id"] != "bad id\n<script>" and len(r.headers["x-request-id"]) == 32


async def test_unknown_route_uses_error_envelope(healthy: httpx.AsyncClient) -> None:
    r = await healthy.get("/api/v1/nope", headers={"X-Request-ID": "rid-0000001"})
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "http_error"
    assert r.json()["error"]["request_id"] == "rid-0000001"
