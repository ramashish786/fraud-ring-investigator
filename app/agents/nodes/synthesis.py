"""7. Evidence synthesizer (LLM). Must cite evidence refs; uncitable claims are kept but flagged (D-012)."""

from __future__ import annotations

import uuid
from typing import Any

from app.core.enums import AssertionLevel
from app.domains.evidence.schemas import FindingIn

from ..deps import WorkflowDeps
from ..prompts.schemas import Claim, PlanOp, SynthesisInput
from ..prompts.tasks import SYNTHESIS
from ..state import InvestigationState
from .investigators import _sanitize


def _claims(entries: list[dict[str, Any]], *keys: str) -> list[Claim]:
    return [Claim(**c) for e in entries for k in keys for c in e.get(k, [])]


async def synthesizer(state: InvestigationState, deps: WorkflowDeps) -> dict[str, Any]:
    inv = uuid.UUID(state["investigation_id"])
    rnd = state.get("round", 0)
    evidence = await deps.digest(inv, state.get("evidence_refs", []))
    evidence = [e for e in evidence if e.kind != "document_passage"]  # passages are summarised by case/policy claims
    inp = SynthesisInput(
        alert_type=state["alert"]["alert_type"],
        alert_summary=state["alert"]["summary"],
        round=rnd,
        max_rounds=deps.settings.workflow_max_rounds,
        executed_operations=state.get("executed_ops", []),
        signals={k: v["value"] for k, v in state.get("signals", {}).items()},
        evidence=evidence,
        notes=state.get("notes", []),
        case_claims=_claims(state.get("historical_cases", []), "similarities", "differences"),
        policy_claims=_claims(state.get("policy_findings", []), "requirements"),
    )
    result = await deps.llm.run(SYNTHESIS, inp, investigation_id=inv)
    out = result.output
    allowed = {e.ref for e in evidence} | {r for c in inp.case_claims + inp.policy_claims for r in c.evidence_refs}
    supporting, f_sup, _ = _sanitize(out.supporting, allowed)
    contradictions, f_con, _ = _sanitize(out.contradictions, allowed)
    for f in f_sup:
        f.category = "supporting"
    for f in f_con:
        f.category = "contradiction"
    f_missing = [
        FindingIn(
            statement=m,
            category="missing_information",
            assertion_level=AssertionLevel.HYPOTHESIS,
            evidence_refs=[],
            is_supported=True,
        )
        for m in out.missing_information
    ]
    await deps.save_findings(inv, f"synthesizer:r{rnd}", f_sup + f_con + f_missing)
    follow = (
        [o.value for o in out.follow_up_operations if o in PlanOp] if rnd < deps.settings.workflow_max_rounds else []
    )
    synthesis = {
        "supporting": [c.model_dump() for c in supporting],
        "contradictions": [c.model_dump() for c in contradictions],
        "missing_information": out.missing_information,
        "narrative": out.narrative,
        "validation_problems": result.problems,
    }
    return {
        "synthesis": synthesis,
        "contradictions": synthesis["contradictions"],
        "missing_information": out.missing_information,
        "follow_up_ops": follow,
    }


def route_after_synthesis(state: InvestigationState) -> str:
    return "planner" if state.get("follow_up_ops") else "risk_assessment"
