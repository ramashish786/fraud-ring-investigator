from __future__ import annotations

import uuid

from sqlalchemy.exc import IntegrityError

from app.core.enums import AlertStatus, Role
from app.core.errors import NotFoundError, PermissionDeniedError
from app.core.pagination import Page
from app.domains.audit.service import record
from app.domains.identity.actor import Actor
from app.infrastructure.database.engine import Database

from .models import Alert
from .repository import AlertRepository
from .schemas import AlertCreate, AlertRead


class AlertService:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def create(self, cmd: AlertCreate, actor: Actor) -> tuple[AlertRead, bool]:
        """Idempotent on external_ref: returns (alert, created)."""
        if not actor.has_any(Role.ANALYST):
            raise PermissionDeniedError("Only analysts can create alerts")
        async with self.db.unit_of_work() as s:
            repo = AlertRepository(s)
            existing = await repo.get_by_external_ref(cmd.external_ref)
            if existing:
                return AlertRead.model_validate(existing), False
            try:
                async with s.begin_nested():  # a concurrent duplicate loses the race cleanly
                    alert = await repo.add(Alert(**cmd.model_dump(), created_by=actor.user_id))
            except IntegrityError:
                existing = await repo.get_by_external_ref(cmd.external_ref)
                assert existing is not None
                return AlertRead.model_validate(existing), False
            await record(
                s,
                actor,
                "alert.created",
                "alert",
                str(alert.id),
                {"external_ref": cmd.external_ref, "alert_type": cmd.alert_type.value},
            )
            return AlertRead.model_validate(alert), True

    async def get(self, alert_id: uuid.UUID) -> AlertRead:
        async with self.db.read_session() as s:
            alert = await AlertRepository(s).get(alert_id)
            if not alert:
                raise NotFoundError("Alert not found")
            return AlertRead.model_validate(alert)

    async def list_page(self, *, status: AlertStatus | None, limit: int, offset: int) -> Page[AlertRead]:
        async with self.db.read_session() as s:
            rows, total = await AlertRepository(s).list_page(status=status, limit=limit, offset=offset)
            return Page(items=[AlertRead.model_validate(a) for a in rows], total=total, limit=limit, offset=offset)
