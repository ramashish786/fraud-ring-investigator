from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import AlertStatus

from .models import Alert


class AlertRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    async def get(self, alert_id: uuid.UUID, *, for_update: bool = False) -> Alert | None:
        stmt = select(Alert).where(Alert.id == alert_id, Alert.deleted_at.is_(None))
        if for_update:
            stmt = stmt.with_for_update()
        return await self.s.scalar(stmt)

    async def get_by_external_ref(self, ref: str) -> Alert | None:
        return await self.s.scalar(select(Alert).where(Alert.external_ref == ref))

    async def add(self, alert: Alert) -> Alert:
        self.s.add(alert)
        await self.s.flush()
        await self.s.refresh(alert)
        return alert

    async def list_page(self, *, status: AlertStatus | None, limit: int, offset: int) -> tuple[list[Alert], int]:
        base = select(Alert).where(Alert.deleted_at.is_(None))
        if status:
            base = base.where(Alert.status == status)
        total = await self.s.scalar(select(func.count()).select_from(base.subquery())) or 0
        rows = (
            await self.s.scalars(base.order_by(Alert.created_at.desc(), Alert.id).limit(limit).offset(offset))
        ).all()
        return list(rows), total
