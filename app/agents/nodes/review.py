"""9. Human review: pause the workflow until a reviewer decides (D-014).

LangGraph re-runs this node from the top on resume, so everything before `interrupt()`
must be side-effect free. Side effects happen only after the decision arrives.
"""

from __future__ import annotations

import uuid
from typing import Any

from langgraph.types import interrupt

from app.core.enums import AssertionLevel, EvidenceSource, ReviewDecision
from app.domains.evidence.schemas import EvidenceItem

from ..deps import WorkflowDeps
from ..prompts.schemas import GRAPH_OPS
from ..state import InvestigationState


async def human_review(state: InvestigationState, deps: WorkflowDeps) -> dict[str, Any]:
    risk = state.get("risk", {})
    decision: dict[str, Any] = interrupt(
        {
            "investigation_id": state["investigation_id"],
            "risk_score": risk.get("score"),
            "risk_category": risk.get("category"),
            "supporting": len(state.get("synthesis", {}).get("supporting", [])),
            "contradictions": len(state.get("contradictions", [])),
            "question": "Approve, reject or request more work?",
        }
    )
    inv = uuid.UUID(state["investigation_id"])
    d = ReviewDecision(decision["decision"])
    item = EvidenceItem(
        kind="human_review",
        assertion_level=AssertionLevel.CONFIRMED_OUTCOME,
        source=EvidenceSource.HUMAN,
        source_ref={"review_id": decision.get("review_id")},
        snapshot={"decision": d.value, "comment": decision.get("comment", ""), "round": state.get("round", 0)},
        summary=f"Human reviewer decision: {d.value}"
        + (f" ({decision['comment']})" if decision.get("comment") else ""),
    )
    refs = await deps.save_evidence(inv, [item], "human_review")
    await deps.event(inv, "human_review", "completed", round_=state.get("round", 0), detail={"decision": d.value})
    follow = (
        sorted(op.value for op in GRAPH_OPS)
        if d == ReviewDecision.NEEDS_MORE_WORK and state.get("round", 0) < deps.settings.workflow_max_rounds
        else []
    )
    return {
        "review": {"decision": d.value, "comment": decision.get("comment", ""), "review_id": decision.get("review_id")},
        "evidence_refs": refs,
        "follow_up_ops": follow,
    }


def route_after_review(state: InvestigationState) -> str:
    return "planner" if state.get("follow_up_ops") else "report_generator"
