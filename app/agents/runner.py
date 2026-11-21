"""Runs an investigation workflow for a job (called by the worker, never by the API).

start:   QUEUED/RUNNING → run from START, or continue from the last checkpoint after a crash.
resume:  AWAITING_REVIEW → RUNNING → Command(resume=decision), or continue after a crash mid-resume.
After each run the checkpoint decides the status: paused at review → AWAITING_REVIEW; finished →
already COMPLETED by the completion node.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from app.core.enums import ActorType, InvestigationStatus
from app.domains.investigations.repository import InvestigationRepository
from app.infrastructure.database.engine import Database
from app.infrastructure.observability.logging import get_logger
from app.infrastructure.observability.metrics import TIME_TO_REVIEW

log = get_logger("workflow.runner")


class InvestigationRunner:
    def __init__(self, db: Database, graph: CompiledStateGraph[Any, Any, Any, Any]) -> None:
        self.db = db
        self.graph = graph

    @staticmethod
    def _config(inv_id: uuid.UUID) -> RunnableConfig:
        return {"configurable": {"thread_id": str(inv_id)}, "recursion_limit": 60}

    async def _set_running(self, inv_id: uuid.UUID, reason: str) -> InvestigationStatus:
        async with self.db.unit_of_work() as s:
            repo = InvestigationRepository(s)
            inv = await repo.get(inv_id, for_update=True)
            if inv is None:
                raise LookupError(f"investigation {inv_id} not found")
            status = InvestigationStatus(inv.status)
            if status in (InvestigationStatus.QUEUED, InvestigationStatus.AWAITING_REVIEW):
                await repo.transition(inv, InvestigationStatus.RUNNING, actor_type=ActorType.SYSTEM, reason=reason)
            return status

    async def _paused(self, inv_id: uuid.UUID) -> bool:
        snap = await self.graph.aget_state(self._config(inv_id))
        return any(t.interrupts for t in snap.tasks)

    async def _after_run(self, inv_id: uuid.UUID) -> InvestigationStatus:
        if await self._paused(inv_id):
            async with self.db.unit_of_work() as s:
                repo = InvestigationRepository(s)
                inv = await repo.get(inv_id, for_update=True)
                assert inv is not None
                if inv.status == InvestigationStatus.RUNNING:
                    await repo.transition(
                        inv,
                        InvestigationStatus.AWAITING_REVIEW,
                        actor_type=ActorType.SYSTEM,
                        reason="waiting for human review",
                    )
                    await repo.add_event(inv_id, "human_review", "interrupted", round_=inv.round)
                    TIME_TO_REVIEW.observe((datetime.now(UTC) - inv.created_at).total_seconds())
            return InvestigationStatus.AWAITING_REVIEW
        async with self.db.read_session() as s:
            inv = await InvestigationRepository(s).get(inv_id)
            assert inv is not None
            return InvestigationStatus(inv.status)

    async def start(self, inv_id: uuid.UUID) -> InvestigationStatus:
        prior = await self._set_running(inv_id, "worker started")
        if prior in InvestigationStatus.terminal():
            return prior
        cfg = self._config(inv_id)
        snap = await self.graph.aget_state(cfg)
        if any(t.interrupts for t in snap.tasks):
            return await self._after_run(inv_id)  # already paused: nothing to do
        payload: Any = None if snap.next else {"investigation_id": str(inv_id)}  # None = continue from checkpoint
        log.info("workflow.start", investigation_id=str(inv_id), from_checkpoint=payload is None)
        await self.graph.ainvoke(payload, cfg)
        return await self._after_run(inv_id)

    async def resume(self, inv_id: uuid.UUID, decision: dict[str, Any]) -> InvestigationStatus:
        prior = await self._set_running(inv_id, f"review: {decision.get('decision')}")
        if prior in InvestigationStatus.terminal():
            return prior
        cfg = self._config(inv_id)
        payload: Any = Command(resume=decision) if await self._paused(inv_id) else None
        log.info(
            "workflow.resume",
            investigation_id=str(inv_id),
            decision=decision.get("decision"),
            from_checkpoint=payload is None,
        )
        await self.graph.ainvoke(payload, cfg)
        return await self._after_run(inv_id)
