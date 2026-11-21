from __future__ import annotations

from typing import Literal

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.api.deps import ContainerDep
from app.infrastructure.observability.health import Pingable, check_readiness

router = APIRouter(prefix="/health", tags=["health"])


class LivenessResponse(BaseModel):
    status: Literal["ok"] = "ok"
    service: str
    version: str


class DependencyStatus(BaseModel):
    name: str
    status: Literal["ok", "error"]
    latency_ms: float | None = None
    error: str | None = None


class ReadinessResponse(BaseModel):
    status: Literal["ready", "not_ready"]
    checks: list[DependencyStatus]


@router.get("", response_model=LivenessResponse, summary="Liveness: process is up")
async def liveness(c: ContainerDep) -> LivenessResponse:
    return LivenessResponse(service=c.settings.app_name, version=c.settings.app_version)


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    summary="Readiness: PostgreSQL and Neo4j reachable",
    responses={503: {"model": ReadinessResponse}},
)
async def readiness(c: ContainerDep) -> JSONResponse:
    deps: dict[str, Pingable] = {"postgres": c.db}
    if c.neo4j is not None:
        deps["neo4j"] = c.neo4j
    report = await check_readiness(deps, c.settings.health_check_timeout_s)
    body = ReadinessResponse(
        status="ready" if report.ready else "not_ready",
        checks=[
            DependencyStatus(name=r.name, status=r.status, latency_ms=r.latency_ms, error=r.error)
            for r in report.checks
        ],
    )
    return JSONResponse(status_code=200 if report.ready else 503, content=body.model_dump())
