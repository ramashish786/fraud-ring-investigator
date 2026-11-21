"""Investigation endpoints. Long-running work happens in the worker; these return quickly."""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Header, Query, Response, status
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from app.api.deps import Analyst, ContainerDep, Reader, Reviewer
from app.core.enums import AssertionLevel, InvestigationStatus
from app.core.errors import NotFoundError
from app.core.pagination import Page
from app.domains.evidence.graph_view import build_graph
from app.domains.evidence.repository import EvidenceRepository
from app.domains.evidence.schemas import EvidenceRead, FindingRead
from app.domains.evidence.service import EvidenceService
from app.domains.investigations.schemas import (
    InvestigationAccepted,
    InvestigationCreate,
    InvestigationDetail,
    InvestigationRead,
    ReportRead,
    ReviewCreate,
    ReviewRead,
    TimelineEntry,
)
from app.domains.investigations.service import InvestigationService
from app.graph.models import Subgraph

router = APIRouter(prefix="/investigations", tags=["investigations"])
IdempotencyKey = Annotated[str | None, Header(alias="Idempotency-Key", max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")]


class CancelRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


@router.post(
    "",
    response_model=InvestigationAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Start an investigation (asynchronous; poll the status URL)",
)
async def start_investigation(
    body: InvestigationCreate,
    c: ContainerDep,
    actor: Analyst,
    response: Response,
    idempotency_key: IdempotencyKey = None,
) -> InvestigationAccepted:
    inv, created = await InvestigationService(c.db).start(body.alert_id, actor, idempotency_key)
    url = f"/api/v1/investigations/{inv.id}"
    response.headers["Location"] = url
    if not created:
        response.headers["Idempotent-Replay"] = "true"
    return InvestigationAccepted(investigation_id=inv.id, status=inv.status, status_url=url)


@router.get("", response_model=Page[InvestigationRead])
async def list_investigations(
    c: ContainerDep,
    _: Reader,
    status_: Annotated[InvestigationStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[InvestigationRead]:
    return await InvestigationService(c.db).list_page(status=status_, limit=limit, offset=offset)


@router.get("/{inv_id}", response_model=InvestigationDetail)
async def get_investigation(inv_id: uuid.UUID, c: ContainerDep, _: Reader) -> InvestigationDetail:
    return await InvestigationService(c.db).get(inv_id)


@router.get("/{inv_id}/evidence", response_model=Page[EvidenceRead])
async def list_evidence(
    inv_id: uuid.UUID,
    c: ContainerDep,
    actor: Reader,
    level: Annotated[list[AssertionLevel] | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[EvidenceRead]:
    await InvestigationService(c.db).get(inv_id)  # 404 if missing
    return await EvidenceService(c.db).list_page(inv_id, actor, levels=level, limit=limit, offset=offset)


@router.get("/{inv_id}/findings", response_model=list[FindingRead])
async def list_findings(inv_id: uuid.UUID, c: ContainerDep, _: Reader) -> list[FindingRead]:
    return await EvidenceService(c.db).findings(inv_id)


@router.get("/{inv_id}/graph", response_model=Subgraph, summary="Network view built from the evidence snapshots")
async def investigation_graph(inv_id: uuid.UUID, c: ContainerDep, actor: Reader) -> Subgraph:
    inv = await InvestigationService(c.db).get(inv_id)
    async with c.db.read_session() as s:
        rows, _ = await EvidenceRepository(s).list_page(inv_id, limit=1000)
    return build_graph(inv.alert.subject_account_id, rows, actor)


@router.get("/{inv_id}/timeline", response_model=list[TimelineEntry])
async def timeline(inv_id: uuid.UUID, c: ContainerDep, _: Reader) -> list[TimelineEntry]:
    return await InvestigationService(c.db).timeline(inv_id)


@router.post(
    "/{inv_id}/review",
    response_model=ReviewRead,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Approve, reject or request more work; the workflow resumes in the worker",
)
async def review(inv_id: uuid.UUID, body: ReviewCreate, c: ContainerDep, actor: Reviewer) -> ReviewRead:
    return await InvestigationService(c.db).submit_review(inv_id, body, actor)


@router.post("/{inv_id}/cancel", response_model=InvestigationRead)
async def cancel(inv_id: uuid.UUID, body: CancelRequest, c: ContainerDep, actor: Reviewer) -> InvestigationRead:
    return await InvestigationService(c.db).cancel(inv_id, actor, body.reason)


@router.get(
    "/{inv_id}/report",
    response_model=ReportRead,
    responses={200: {"content": {"text/markdown": {}}}, 404: {"description": "No report yet"}},
)
async def report(
    inv_id: uuid.UUID,
    c: ContainerDep,
    _: Reader,
    fmt: Annotated[Literal["json", "markdown"], Query(alias="format")] = "json",
) -> ReportRead | PlainTextResponse:
    rep = await InvestigationService(c.db).report(inv_id)
    if fmt == "markdown":
        return PlainTextResponse(rep.markdown, media_type="text/markdown")
    return rep


__all__ = ["NotFoundError", "router"]
