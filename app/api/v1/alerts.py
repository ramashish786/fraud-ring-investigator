from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Response, status

from app.api.deps import Analyst, ContainerDep, Reader
from app.core.enums import AlertStatus
from app.core.pagination import Page
from app.domains.alerts.schemas import AlertCreate, AlertRead
from app.domains.alerts.service import AlertService

router = APIRouter(prefix="/alerts", tags=["alerts"])


@router.post(
    "",
    response_model=AlertRead,
    status_code=status.HTTP_201_CREATED,
    responses={200: {"description": "Alert with this external_ref already exists (idempotent)"}},
)
async def create_alert(body: AlertCreate, c: ContainerDep, actor: Analyst, response: Response) -> AlertRead:
    alert, created = await AlertService(c.db).create(body, actor)
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    response.headers["Location"] = f"/api/v1/alerts/{alert.id}"
    return alert


@router.get("", response_model=Page[AlertRead])
async def list_alerts(
    c: ContainerDep,
    _: Reader,
    status_: Annotated[AlertStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[AlertRead]:
    return await AlertService(c.db).list_page(status=status_, limit=limit, offset=offset)


@router.get("/{alert_id}", response_model=AlertRead)
async def get_alert(alert_id: uuid.UUID, c: ContainerDep, _: Reader) -> AlertRead:
    return await AlertService(c.db).get(alert_id)
