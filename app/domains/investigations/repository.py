from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import ActorType, InvestigationStatus
from app.domains.reports.models import Report
from app.domains.reviews.models import Review

from .models import Investigation, InvestigationEvent, InvestigationStatusHistory, RiskAssessment
from .state_machine import assert_transition


class InvestigationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    async def get(self, inv_id: uuid.UUID, *, for_update: bool = False) -> Investigation | None:
        stmt = select(Investigation).where(Investigation.id == inv_id, Investigation.deleted_at.is_(None))
        if for_update:
            stmt = stmt.with_for_update()
        return await self.s.scalar(stmt)

    async def get_by_idempotency_key(self, key: str) -> Investigation | None:
        return await self.s.scalar(select(Investigation).where(Investigation.idempotency_key == key))

    async def active_for_alert(self, alert_id: uuid.UUID) -> Investigation | None:
        return await self.s.scalar(
            select(Investigation).where(
                Investigation.alert_id == alert_id,
                Investigation.deleted_at.is_(None),
                Investigation.status.not_in(InvestigationStatus.terminal()),
            )
        )

    async def add(self, inv: Investigation) -> Investigation:
        self.s.add(inv)
        await self.s.flush()
        await self.s.refresh(inv)
        return inv

    async def list_page(
        self, *, status: InvestigationStatus | None, limit: int, offset: int
    ) -> tuple[list[Investigation], int]:
        base = select(Investigation).where(Investigation.deleted_at.is_(None))
        if status:
            base = base.where(Investigation.status == status)
        total = await self.s.scalar(select(func.count()).select_from(base.subquery())) or 0
        rows = (
            await self.s.scalars(
                base.order_by(Investigation.created_at.desc(), Investigation.id).limit(limit).offset(offset)
            )
        ).all()
        return list(rows), total

    async def transition(
        self,
        inv: Investigation,
        target: InvestigationStatus,
        *,
        actor_type: ActorType,
        actor_user_id: uuid.UUID | None = None,
        reason: str | None = None,
    ) -> None:
        current = InvestigationStatus(inv.status)
        assert_transition(current, target)
        now = datetime.now(UTC)
        inv.status = target
        if target == InvestigationStatus.RUNNING and inv.started_at is None:
            inv.started_at = now
        if target in InvestigationStatus.terminal():
            inv.completed_at = now
        self.s.add(
            InvestigationStatusHistory(
                investigation_id=inv.id,
                from_status=current,
                to_status=target,
                reason=reason,
                actor_type=actor_type,
                actor_user_id=actor_user_id,
            )
        )
        await self.s.flush()

    async def history(self, inv_id: uuid.UUID) -> list[InvestigationStatusHistory]:
        return list(
            (
                await self.s.scalars(
                    select(InvestigationStatusHistory)
                    .where(InvestigationStatusHistory.investigation_id == inv_id)
                    .order_by(InvestigationStatusHistory.id)
                )
            ).all()
        )

    async def events(self, inv_id: uuid.UUID) -> list[InvestigationEvent]:
        return list(
            (
                await self.s.scalars(
                    select(InvestigationEvent)
                    .where(InvestigationEvent.investigation_id == inv_id)
                    .order_by(InvestigationEvent.id)
                )
            ).all()
        )

    async def add_event(
        self,
        inv_id: uuid.UUID,
        node: str,
        status: str,
        *,
        round_: int = 0,
        duration_ms: int | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        self.s.add(
            InvestigationEvent(
                investigation_id=inv_id,
                node=node,
                status=status,
                round=round_,
                duration_ms=duration_ms,
                detail=detail or {},
            )
        )
        await self.s.flush()

    async def reviews(self, inv_id: uuid.UUID) -> list[Review]:
        return list(
            (
                await self.s.scalars(
                    select(Review).where(Review.investigation_id == inv_id).order_by(Review.created_at)
                )
            ).all()
        )

    async def latest_risk(self, inv_id: uuid.UUID) -> RiskAssessment | None:
        return await self.s.scalar(
            select(RiskAssessment)
            .where(RiskAssessment.investigation_id == inv_id)
            .order_by(RiskAssessment.created_at.desc())
            .limit(1)
        )

    async def latest_report(self, inv_id: uuid.UUID) -> Report | None:
        return await self.s.scalar(
            select(Report).where(Report.investigation_id == inv_id).order_by(Report.version.desc()).limit(1)
        )

    async def next_report_version(self, inv_id: uuid.UUID) -> int:
        current = await self.s.scalar(select(func.max(Report.version)).where(Report.investigation_id == inv_id))
        return (current or 0) + 1
