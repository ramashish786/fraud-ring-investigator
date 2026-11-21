"""2. Investigation planner (LLM, constrained). Falls back to the deterministic plan on any LLM failure."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import text

from app.infrastructure.observability.logging import get_logger

from ..deps import WorkflowDeps
from ..prompts.schemas import InvestigationPlan, PlannerInput, PlanOp
from ..prompts.tasks import MANDATORY_OPS, PLANNER, default_plan
from ..state import InvestigationState

log = get_logger("workflow.planner")
BRANCHES = {"transaction_investigator", "graph_investigator", "case_retriever", "policy_retriever"}


async def planner(state: InvestigationState, deps: WorkflowDeps) -> dict[str, Any]:
    follow_up = state.get("follow_up_ops", [])
    rnd = state.get("round", 0) + (1 if follow_up and state.get("plan") else 0)
    alert = state["alert"]
    inp = PlannerInput(
        alert_type=alert["alert_type"],
        severity=alert["severity"],
        summary=alert["summary"],
        round=rnd,
        follow_up_requests=follow_up,
        already_executed=state.get("executed_ops", []),
    )
    errors: list[dict[str, Any]] = []
    try:
        plan = (await deps.llm.run(PLANNER, inp, investigation_id=uuid.UUID(state["investigation_id"]))).output
    except Exception as exc:
        log.warning("planner.fallback", error_type=type(exc).__name__)
        errors.append({"node": "planner", "error": type(exc).__name__, "handled": "deterministic plan"})
        plan = default_plan(inp)
    ops = list(dict.fromkeys(plan.operations))
    if rnd == 0:  # the LLM may add checks but never drop mandatory ones
        ops = list(dict.fromkeys([*ops, *sorted(MANDATORY_OPS)]))
    plan = InvestigationPlan(**{**plan.model_dump(), "operations": ops})
    async with deps.db.unit_of_work() as s:
        await s.execute(
            text("UPDATE investigations SET round = :r WHERE id = :i"),
            {"r": rnd, "i": uuid.UUID(state["investigation_id"])},
        )
    return {"plan": plan.model_dump(mode="json"), "round": rnd, "follow_up_ops": [], "errors": errors}


def route_after_planner(state: InvestigationState) -> list[str]:
    ops = {PlanOp(o) for o in state["plan"]["operations"]}
    targets = []
    if PlanOp.TRANSACTION_ANALYSIS in ops:
        targets.append("transaction_investigator")
    if ops & {
        PlanOp.MONEY_TRAIL,
        PlanOp.SHARED_DEVICES,
        PlanOp.SHARED_IPS,
        PlanOp.BENEFICIARY_NETWORK,
        PlanOp.PRIOR_CASES,
    }:
        targets.append("graph_investigator")
    if PlanOp.HISTORICAL_CASES in ops:
        targets.append("case_retriever")
    if PlanOp.POLICY_LOOKUP in ops:
        targets.append("policy_retriever")
    return targets or ["graph_investigator"]
