"""3-6. Investigators: deterministic analysis + (for 5/6) one constrained LLM interpretation each.

Transient graph/retrieval failures are retried, then degraded: the node records the
error and a missing-information note, and the investigation continues (spec §9).
"""

from __future__ import annotations

import uuid
from typing import Any, cast

from app.core.enums import AssertionLevel, Classification
from app.domains.analysis.network import NetworkOp, analyze_network
from app.domains.analysis.retrieval import retrieve
from app.domains.analysis.signals import AnalysisResult
from app.domains.analysis.transactions import analyze_transactions
from app.domains.evidence.schemas import FindingIn
from app.infrastructure.observability.logging import get_logger
from app.infrastructure.observability.metrics import NODE_DEGRADED

from ..deps import WorkflowDeps, with_retry
from ..prompts.schemas import GRAPH_OPS, Claim, EvidenceDigest, PassageAnalysisInput, PlanOp
from ..prompts.tasks import CASE_ANALYSIS, POLICY_ANALYSIS
from ..state import InvestigationState

log = get_logger("workflow.investigators")


def _degraded(node: str, exc: Exception, what: str) -> dict[str, Any]:
    log.warning("investigator.degraded", node=node, error_type=type(exc).__name__)
    NODE_DEGRADED.labels(node).inc()
    return {
        "errors": [{"node": node, "error": type(exc).__name__, "handled": "degraded"}],
        "notes": [f"{what} could not be completed ({type(exc).__name__}); conclusions exclude it"],
    }


async def _persist(
    deps: WorkflowDeps, state: InvestigationState, res: AnalysisResult, node: str
) -> tuple[list[str], dict[str, dict[str, Any]]]:
    refs = await deps.save_evidence(uuid.UUID(state["investigation_id"]), res.evidence, node)
    return refs, {k: v.model_dump() for k, v in res.signals.items()}


async def transaction_investigator(state: InvestigationState, deps: WorkflowDeps) -> dict[str, Any]:
    subject = state["alert"]["subject_account_id"]
    try:
        res = await with_retry(lambda: analyze_transactions(deps.graph, subject))
    except Exception as exc:
        return _degraded("transaction_investigator", exc, "Transaction analysis")
    refs, signals = await _persist(deps, state, res, "transaction_investigator")
    return {
        "transaction_findings": [{"round": state.get("round", 0), "evidence_refs": refs, "notes": res.notes}],
        "signals": signals,
        "evidence_refs": refs,
        "notes": res.notes,
        "executed_ops": ["transaction_analysis"],
    }


async def graph_investigator(state: InvestigationState, deps: WorkflowDeps) -> dict[str, Any]:
    subject = state["alert"]["subject_account_id"]
    ops = [o for o in state["plan"]["operations"] if PlanOp(o) in GRAPH_OPS]
    extra = [a for a in state.get("network_accounts", []) if a != subject][:10]
    try:
        res = await with_retry(lambda: analyze_network(deps.graph, subject, cast(list[NetworkOp], ops), extra))
    except Exception as exc:
        return _degraded("graph_investigator", exc, "Graph analysis")
    refs, signals = await _persist(deps, state, res, "graph_investigator")
    return {
        "graph_findings": [
            {"round": state.get("round", 0), "operations": ops, "evidence_refs": refs, "notes": res.notes}
        ],
        "signals": signals,
        "evidence_refs": refs,
        "notes": res.notes,
        "executed_ops": ops,
        "network_accounts": res.network_accounts,
    }


def _sanitize(claims: list[Claim], allowed: set[str]) -> tuple[list[Claim], list[FindingIn], int]:
    """Keep only citable refs (D-012). Claims left without evidence are kept but flagged unsupported."""
    kept, findings, unsupported = [], [], 0
    for c in claims:
        refs = [r for r in c.evidence_refs if r in allowed]
        if not refs:
            unsupported += 1
        kept.append(Claim(statement=c.statement, evidence_refs=refs))
        findings.append(
            FindingIn(
                statement=c.statement,
                category="",
                assertion_level=AssertionLevel.HYPOTHESIS,
                evidence_refs=refs,
                is_supported=bool(refs),
            )
        )
    return kept, findings, unsupported


def _key_signals(state: InvestigationState) -> dict[str, float]:
    return {k: v["value"] for k, v in state.get("signals", {}).items() if v.get("value")}


async def case_retriever(state: InvestigationState, deps: WorkflowDeps) -> dict[str, Any]:
    inv = uuid.UUID(state["investigation_id"])
    rnd = state.get("round", 0)
    try:
        res = await with_retry(
            lambda: retrieve(deps.search, state["plan"]["case_query"], "case", Classification(state["clearance"]))
        )
    except Exception as exc:
        return _degraded("case_retriever", exc, "Historical case retrieval")
    refs = await deps.save_evidence(inv, res.evidence, "case_retriever")
    passages = [
        EvidenceDigest(
            ref=e.ref, kind=e.kind, level=e.assertion_level.value, summary=e.summary, text=e.snapshot.get("text")
        )
        for e in res.evidence
    ]
    errors: list[dict[str, Any]] = []
    sims: list[Claim] = []
    diffs: list[Claim] = []
    if passages:
        try:
            out = (
                await deps.llm.run(
                    CASE_ANALYSIS,
                    PassageAnalysisInput(
                        alert_summary=state["alert"]["summary"], key_signals=_key_signals(state), passages=passages
                    ),
                    investigation_id=inv,
                )
            ).output
            allowed = set(refs)
            sims, f1, _ = _sanitize(out.similarities, allowed)
            diffs, f2, _ = _sanitize(out.differences, allowed)
            for f in f1:
                f.category = "similarity"
            for f in f2:
                f.category = "difference"
            await deps.save_findings(inv, f"case_analysis:r{rnd}", f1 + f2)
        except Exception as exc:
            errors.append({"node": "case_retriever", "error": type(exc).__name__, "handled": "no LLM analysis"})
    return {
        "historical_cases": [
            {
                "round": rnd,
                "evidence_refs": refs,
                "similarities": [c.model_dump() for c in sims],
                "differences": [c.model_dump() for c in diffs],
            }
        ],
        "evidence_refs": refs,
        "notes": res.notes,
        "executed_ops": ["historical_cases"],
        "errors": errors,
    }


async def policy_retriever(state: InvestigationState, deps: WorkflowDeps) -> dict[str, Any]:
    inv = uuid.UUID(state["investigation_id"])
    rnd = state.get("round", 0)
    try:
        res = await with_retry(
            lambda: retrieve(deps.search, state["plan"]["policy_query"], "guidance", Classification(state["clearance"]))
        )
    except Exception as exc:
        return _degraded("policy_retriever", exc, "Policy retrieval")
    refs = await deps.save_evidence(inv, res.evidence, "policy_retriever")
    passages = [
        EvidenceDigest(
            ref=e.ref, kind=e.kind, level=e.assertion_level.value, summary=e.summary, text=e.snapshot.get("text")
        )
        for e in res.evidence
    ]
    reqs: list[Claim] = []
    errors: list[dict[str, Any]] = []
    if passages:
        try:
            out = (
                await deps.llm.run(
                    POLICY_ANALYSIS,
                    PassageAnalysisInput(
                        alert_summary=state["alert"]["summary"], key_signals=_key_signals(state), passages=passages
                    ),
                    investigation_id=inv,
                )
            ).output
            reqs, findings, _ = _sanitize(out.requirements, set(refs))
            for f in findings:
                f.category = "requirement"
            await deps.save_findings(inv, f"policy_analysis:r{rnd}", findings)
        except Exception as exc:
            errors.append({"node": "policy_retriever", "error": type(exc).__name__, "handled": "no LLM analysis"})
    return {
        "policy_findings": [{"round": rnd, "evidence_refs": refs, "requirements": [c.model_dump() for c in reqs]}],
        "evidence_refs": refs,
        "notes": res.notes,
        "executed_ops": ["policy_lookup"],
        "errors": errors,
    }
