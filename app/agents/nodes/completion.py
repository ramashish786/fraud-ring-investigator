"""11. Audit and completion (deterministic)."""

from __future__ import annotations

import uuid
from typing import Any

from app.core.enums import ActorType, AlertStatus, InvestigationStatus
from app.domains.alerts.repository import AlertRepository
from app.domains.audit.service import record
from app.domains.identity.actor import Actor
from app.domains.investigations.repository import InvestigationRepository
from app.infrastructure.observability.logging import get_logger
from app.infrastructure.observability.metrics import INVESTIGATIONS_FINISHED

from ..deps import WorkflowDeps
from ..state import InvestigationState

log = get_logger("workflow.completion")


async def audit_completion(state: InvestigationState, deps: WorkflowDeps) -> dict[str, Any]:
    inv_id = uuid.UUID(state["investigation_id"])
    errors: list[dict[str, Any]] = []
    async with deps.db.unit_of_work() as s:
        repo = InvestigationRepository(s)
        inv = await repo.get(inv_id, for_update=True)
        assert inv is not None
        if inv.status != InvestigationStatus.COMPLETED:  # idempotent on re-execution
            await repo.transition(
                inv,
                InvestigationStatus.COMPLETED,
                actor_type=ActorType.SYSTEM,
                reason=f"review: {state.get('review', {}).get('decision', 'none')}",
            )
            alert = await AlertRepository(s).get(inv.alert_id)
            if alert:
                alert.status = AlertStatus.CLOSED
            await record(
                s,
                Actor.system(),
                "investigation.completed",
                "investigation",
                str(inv_id),
                {
                    "risk_score": state["risk"]["score"],
                    "risk_category": state["risk"]["category"],
                    "review_decision": state.get("review", {}).get("decision"),
                    "report_version": state.get("report_version"),
                },
            )
            INVESTIGATIONS_FINISHED.labels("completed").inc()
    try:  # graph mirror is best effort (D-005); Postgres is the system of record
        await deps.graph.upsert_investigation(str(inv_id), "completed", state.get("network_accounts", [])[:50])
    except Exception as exc:
        log.warning("completion.graph_mirror_failed", error_type=type(exc).__name__)
        errors.append({"node": "audit_completion", "error": type(exc).__name__, "handled": "mirror skipped"})
    return {"status": "completed", "errors": errors}
