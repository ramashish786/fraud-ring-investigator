"""10. Report generator (LLM narrative + deterministic risk, review and evidence sections)."""

from __future__ import annotations

import uuid
from typing import Any

from app.domains.investigations.repository import InvestigationRepository
from app.domains.reports.models import Report
from app.domains.reports.render import render_markdown

from ..deps import WorkflowDeps
from ..prompts.schemas import Claim, ReportInput, Synthesis
from ..prompts.tasks import REPORT
from ..state import InvestigationState
from .investigators import _sanitize
from .synthesis import _claims


async def report_generator(state: InvestigationState, deps: WorkflowDeps) -> dict[str, Any]:
    inv = uuid.UUID(state["investigation_id"])
    evidence = await deps.digest(inv, state.get("evidence_refs", []))
    risk, review, syn = state["risk"], state.get("review", {}), state.get("synthesis", {})
    inp = ReportInput(
        alert=state["alert"],
        risk_score=risk["score"],
        risk_category=risk["category"],
        risk_factors=[f"{f['rule_id']}: {f['description']}" for f in risk["factors"]],
        review_decision=review.get("decision", "none"),
        review_comment=review.get("comment", ""),
        synthesis=Synthesis(
            supporting=[Claim(**c) for c in syn.get("supporting", [])],
            contradictions=[Claim(**c) for c in syn.get("contradictions", [])],
            missing_information=syn.get("missing_information", []),
            follow_up_operations=[],
            narrative=syn.get("narrative", ""),
        ),
        case_analysis=_claims(state.get("historical_cases", []), "similarities"),
        policy_requirements=_claims(state.get("policy_findings", []), "requirements"),
        evidence=evidence,
    )
    result = await deps.llm.run(REPORT, inp, investigation_id=inv)
    narrative = result.output.model_dump()
    allowed = {e.ref for e in evidence} | {
        r for c in inp.case_analysis + inp.policy_requirements for r in c.evidence_refs
    }
    unsupported = 0
    for key in (
        "transaction_patterns",
        "graph_relationships",
        "historical_case_similarities",
        "supporting_evidence",
        "contradictions",
    ):
        kept, _, bad = _sanitize([Claim(**c) for c in narrative[key]], allowed)
        narrative[key] = [c.model_dump() for c in kept]
        unsupported += bad
    narrative["suspicious_entities"] = [
        {**e, "evidence_refs": [r for r in e["evidence_refs"] if r in allowed]}
        for e in narrative["suspicious_entities"]
        if any(r in allowed for r in e["evidence_refs"])
    ]
    async with deps.db.unit_of_work() as s:
        version = await InvestigationRepository(s).next_report_version(inv)
        content = {
            "version": version,
            "alert": state["alert"],
            "narrative": narrative,
            "risk_assessment": risk,
            "human_review": review or {"decision": "none"},
            "plan": state.get("plan", {}),
            "rounds": state.get("round", 0),
            "evidence": [e.model_dump(exclude={"text"}) for e in evidence],
            "errors": state.get("errors", []),
            "generation": {
                "model": result.model,
                "attempts": result.attempts,
                "validation_problems": len(result.problems),
                "unsupported_claims": unsupported,
            },
            "assertion_levels": [
                "verified_fact",
                "observed_relationship",
                "inferred_pattern",
                "hypothesis",
                "confirmed_outcome",
            ],
        }
        s.add(
            Report(
                investigation_id=inv,
                version=version,
                content=content,
                markdown=render_markdown(content),
                ruleset_version=risk.get("ruleset_version"),
                generated_by_model=result.model,
            )
        )
    return {"report_version": version}
