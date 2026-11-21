"""Investigation lifecycle use cases (API side).

The API never runs the workflow. `start` writes the investigation, its status history,
a `start_investigation` job and an audit entry in ONE transaction (transactional outbox,
D-007) and returns immediately; the worker picks the job up.
"""

from __future__ import annotations

import uuid

from app.core.enums import ActorType, AlertStatus, InvestigationStatus, JobKind, Role
from app.core.errors import ConflictError, NotFoundError, PermissionDeniedError
from app.core.pagination import Page
from app.domains.alerts.repository import AlertRepository
from app.domains.alerts.schemas import AlertRead
from app.domains.audit.service import record
from app.domains.identity.actor import Actor
from app.domains.reviews.models import Review
from app.infrastructure.database.engine import Database
from app.infrastructure.jobs.repository import JobRepository
from app.infrastructure.observability.metrics import INVESTIGATIONS_FINISHED, INVESTIGATIONS_STARTED

from .models import Investigation
from .repository import InvestigationRepository
from .schemas import (
    InvestigationDetail,
    InvestigationRead,
    ReportRead,
    ReviewCreate,
    ReviewRead,
    RiskAssessmentRead,
    RiskFactor,
    TimelineEntry,
)


class InvestigationService:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def start(
        self, alert_id: uuid.UUID, actor: Actor, idempotency_key: str | None
    ) -> tuple[InvestigationRead, bool]:
        if not actor.has_any(Role.ANALYST):
            raise PermissionDeniedError("Only analysts can start investigations")
        if actor.user_id is None:
            raise PermissionDeniedError("A user is required")
        async with self.db.unit_of_work() as s:
            invs = InvestigationRepository(s)
            if idempotency_key:
                prior = await invs.get_by_idempotency_key(idempotency_key)
                if prior:
                    if prior.alert_id != alert_id:
                        raise ConflictError("Idempotency-Key was already used for a different alert")
                    return InvestigationRead.model_validate(prior), False
            alert = await AlertRepository(s).get(alert_id, for_update=True)  # serialises concurrent starts
            if not alert:
                raise NotFoundError("Alert not found")
            active = await invs.active_for_alert(alert_id)
            if active:
                raise ConflictError(
                    "An investigation is already active for this alert", details={"investigation_id": str(active.id)}
                )
            inv = await invs.add(
                Investigation(alert_id=alert_id, created_by=actor.user_id, idempotency_key=idempotency_key)
            )
            await invs.add_event(inv.id, "api", "queued")
            alert.status = AlertStatus.UNDER_INVESTIGATION
            await JobRepository(s).enqueue(JobKind.START_INVESTIGATION, investigation_id=inv.id)
            await record(s, actor, "investigation.created", "investigation", str(inv.id), {"alert_id": str(alert_id)})
            result = InvestigationRead.model_validate(inv)
        INVESTIGATIONS_STARTED.inc()  # after commit: only count what was persisted
        return result, True

    async def get(self, inv_id: uuid.UUID) -> InvestigationDetail:
        async with self.db.read_session() as s:
            repo = InvestigationRepository(s)
            inv = await repo.get(inv_id)
            if not inv:
                raise NotFoundError("Investigation not found")
            alert = await AlertRepository(s).get(inv.alert_id)
            assert alert is not None
            risk = await repo.latest_risk(inv_id)
            return InvestigationDetail(
                **InvestigationRead.model_validate(inv).model_dump(),
                alert=AlertRead.model_validate(alert),
                risk=RiskAssessmentRead(
                    score=risk.score,
                    category=risk.category,
                    ruleset_version=risk.ruleset_version,
                    factors=[RiskFactor.model_validate(f) for f in risk.factors],
                    created_at=risk.created_at,
                )
                if risk
                else None,
                status_url=f"/api/v1/investigations/{inv_id}",
            )

    async def list_page(
        self, *, status: InvestigationStatus | None, limit: int, offset: int
    ) -> Page[InvestigationRead]:
        async with self.db.read_session() as s:
            rows, total = await InvestigationRepository(s).list_page(status=status, limit=limit, offset=offset)
            return Page(
                items=[InvestigationRead.model_validate(r) for r in rows], total=total, limit=limit, offset=offset
            )

    async def timeline(self, inv_id: uuid.UUID) -> list[TimelineEntry]:
        async with self.db.read_session() as s:
            repo = InvestigationRepository(s)
            if not await repo.get(inv_id):
                raise NotFoundError("Investigation not found")
            entries = [
                TimelineEntry(
                    ts=h.created_at,
                    kind="status",
                    label=f"status → {h.to_status}",
                    detail={"from": h.from_status, "reason": h.reason, "actor": h.actor_type},
                )
                for h in await repo.history(inv_id)
            ]
            entries += [
                TimelineEntry(
                    ts=e.created_at,
                    kind="node",
                    label=f"{e.node}: {e.status}",
                    detail={"round": e.round, "duration_ms": e.duration_ms, **e.detail},
                )
                for e in await repo.events(inv_id)
            ]
            entries += [
                TimelineEntry(
                    ts=r.created_at,
                    kind="review",
                    label=f"review: {r.decision}",
                    detail={"comment": r.comment, "reviewer_id": str(r.reviewer_id)},
                )
                for r in await repo.reviews(inv_id)
            ]
            return sorted(entries, key=lambda e: e.ts)

    async def submit_review(self, inv_id: uuid.UUID, cmd: ReviewCreate, actor: Actor) -> ReviewRead:
        if not actor.has_any(Role.REVIEWER) or actor.user_id is None:
            raise PermissionDeniedError("Only reviewers can review investigations")
        async with self.db.unit_of_work() as s:
            repo = InvestigationRepository(s)
            inv = await repo.get(inv_id, for_update=True)
            if not inv:
                raise NotFoundError("Investigation not found")
            if inv.created_by == actor.user_id:  # four-eyes principle (D-030)
                raise PermissionDeniedError("The analyst who started an investigation cannot review it")
            if inv.status != InvestigationStatus.AWAITING_REVIEW:
                raise ConflictError("Investigation is not awaiting review", details={"status": str(inv.status)})
            jobs = JobRepository(s)
            if await jobs.has_pending(JobKind.RESUME_INVESTIGATION, inv_id):
                raise ConflictError("A review has already been submitted and is being processed")
            review = Review(
                investigation_id=inv_id, reviewer_id=actor.user_id, decision=cmd.decision, comment=cmd.comment
            )
            s.add(review)
            await s.flush()
            await jobs.enqueue(
                JobKind.RESUME_INVESTIGATION,
                investigation_id=inv_id,
                payload={"review_id": str(review.id), "decision": cmd.decision.value, "comment": cmd.comment or ""},
            )
            await repo.add_event(inv_id, "human_review", "submitted", detail={"decision": cmd.decision.value})
            await record(
                s,
                actor,
                f"investigation.review.{cmd.decision.value}",
                "investigation",
                str(inv_id),
                {"review_id": str(review.id)},
            )
            return ReviewRead.model_validate(review)

    async def report(self, inv_id: uuid.UUID) -> ReportRead:
        async with self.db.read_session() as s:
            report = await InvestigationRepository(s).latest_report(inv_id)
            if not report:
                raise NotFoundError("No report yet for this investigation")
            return ReportRead.model_validate(report)

    async def cancel(self, inv_id: uuid.UUID, actor: Actor, reason: str) -> InvestigationRead:
        if not actor.has_any(Role.REVIEWER, Role.ADMIN):
            raise PermissionDeniedError("Only reviewers or admins can cancel investigations")
        async with self.db.unit_of_work() as s:
            repo = InvestigationRepository(s)
            inv = await repo.get(inv_id, for_update=True)
            if not inv:
                raise NotFoundError("Investigation not found")
            await repo.transition(
                inv,
                InvestigationStatus.CANCELLED,
                actor_type=ActorType.USER,
                actor_user_id=actor.user_id,
                reason=reason,
            )
            await record(s, actor, "investigation.cancelled", "investigation", str(inv_id), {"reason": reason})
            result = InvestigationRead.model_validate(inv)
        INVESTIGATIONS_FINISHED.labels("cancelled").inc()
        return result
