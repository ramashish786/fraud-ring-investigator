"""8. Risk assessment (deterministic rules only, D-013). The LLM never touches the score."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import text

from app.domains.analysis.signals import Signal
from app.domains.investigations.models import RiskAssessment
from app.infrastructure.observability.metrics import RISK_CATEGORY

from ..deps import WorkflowDeps
from ..state import InvestigationState


async def risk_assessment(state: InvestigationState, deps: WorkflowDeps) -> dict[str, Any]:
    inv = uuid.UUID(state["investigation_id"])
    signals = {k: Signal(**v) for k, v in state.get("signals", {}).items()}
    result = deps.risk.assess(signals)
    async with deps.db.unit_of_work() as s:
        s.add(
            RiskAssessment(
                investigation_id=inv,
                score=result.score,
                category=result.category,
                ruleset_version=result.ruleset_version,
                factors=[f.model_dump() for f in result.factors],
            )
        )
        await s.execute(
            text("UPDATE investigations SET risk_score = :sc, risk_category = :c WHERE id = :i"),
            {"sc": result.score, "c": result.category.value, "i": inv},
        )
    RISK_CATEGORY.labels(result.category.value).inc()
    return {"risk": result.model_dump(mode="json")}
