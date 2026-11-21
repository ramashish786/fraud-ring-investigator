"""The investigation graph (spec §9).

intake → planner ─┬─ transaction_investigator ─┐
                  ├─ graph_investigator ───────┤
                  ├─ case_retriever ───────────┼─→ synthesizer ─┬─→ planner (bounded follow-up)
                  └─ policy_retriever ─────────┘                └─→ risk_assessment → human_review ⏸
human_review ─┬─→ planner (needs more work, bounded)
              └─→ report_generator → audit_completion → END
"""

from __future__ import annotations

from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from .deps import WorkflowDeps
from .instrumentation import instrumented
from .nodes.completion import audit_completion
from .nodes.intake import intake
from .nodes.investigators import case_retriever, graph_investigator, policy_retriever, transaction_investigator
from .nodes.planner import BRANCHES, planner, route_after_planner
from .nodes.report import report_generator
from .nodes.review import human_review, route_after_review
from .nodes.risk import risk_assessment
from .nodes.synthesis import route_after_synthesis, synthesizer
from .state import InvestigationState

NODE_NAMES = [
    "intake",
    "planner",
    "transaction_investigator",
    "graph_investigator",
    "case_retriever",
    "policy_retriever",
    "synthesizer",
    "risk_assessment",
    "human_review",
    "report_generator",
    "audit_completion",
]


def build_workflow(
    deps: WorkflowDeps, checkpointer: BaseCheckpointSaver[Any] | None
) -> CompiledStateGraph[Any, Any, Any, Any]:
    g: StateGraph[Any, Any, Any, Any] = StateGraph(InvestigationState)
    for name, fn in (
        ("intake", intake),
        ("planner", planner),
        ("transaction_investigator", transaction_investigator),
        ("graph_investigator", graph_investigator),
        ("case_retriever", case_retriever),
        ("policy_retriever", policy_retriever),
        ("synthesizer", synthesizer),
        ("risk_assessment", risk_assessment),
        ("report_generator", report_generator),
        ("audit_completion", audit_completion),
    ):
        g.add_node(name, instrumented(name, fn, deps))  # type: ignore[call-overload]

    async def review_node(state: InvestigationState) -> dict[str, Any]:
        # not wrapped: it re-executes on resume, and its events are written after the decision
        return await human_review(state, deps)

    g.add_node("human_review", review_node)
    g.add_edge(START, "intake")
    g.add_edge("intake", "planner")
    g.add_conditional_edges("planner", route_after_planner, sorted(BRANCHES))
    for branch in BRANCHES:
        g.add_edge(branch, "synthesizer")
    g.add_conditional_edges("synthesizer", route_after_synthesis, ["planner", "risk_assessment"])
    g.add_edge("risk_assessment", "human_review")
    g.add_conditional_edges("human_review", route_after_review, ["planner", "report_generator"])
    g.add_edge("report_generator", "audit_completion")
    g.add_edge("audit_completion", END)
    return g.compile(checkpointer=checkpointer)
