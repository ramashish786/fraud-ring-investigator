"""Metrics: exposed, low-cardinality labels, no raw ids in label values."""

import uuid
from typing import Any, cast

import httpx
from prometheus_client import REGISTRY

from app.core.container import Container
from app.main import create_app


class _Dep:
    async def ping(self, timeout_s: float) -> float:
        return 1.0

    async def close(self) -> None: ...
    async def dispose(self) -> None: ...


async def test_metrics_endpoint_uses_route_templates(make_settings: Any) -> None:
    s = make_settings()
    c = Container(settings=s, db=cast(Any, _Dep()), neo4j=cast(Any, _Dep()))
    app = create_app(container=c)
    app.state.container = c
    inv_id = uuid.uuid4()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
        await client.get("/api/v1/health")
        await client.get(f"/api/v1/investigations/{inv_id}")  # 401, but routed
        await client.get("/totally/unknown")
        body = (await client.get("/metrics")).text
    assert 'fri_http_requests_total{method="GET",route="/api/v1/health",status="200"}' in body
    assert 'route="/api/v1/investigations/{inv_id}",status="401"' in body
    assert 'route="unmatched",status="404"' in body
    assert str(inv_id) not in body  # never a raw id in a label
    assert (
        REGISTRY.get_sample_value("fri_http_requests_total", {"method": "GET", "route": "/metrics", "status": "200"})
        is None
    )  # self-scrapes excluded
