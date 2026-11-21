"""LLM task definitions: system prompt, input rendering, validator and deterministic fake.

Prompt rules shared by every task:
* Only use the evidence provided. Every claim cites evidence refs from the input.
* A shared device or IP is a relationship, not proof; say so when relevant.
* Never state numbers (amounts, scores) that are not in the evidence.
* Write hypotheses as hypotheses ("is consistent with", "may indicate").
"""

from __future__ import annotations

import re

from app.infrastructure.llm.service import LLMTask, as_json

from .schemas import (
    CaseAnalysis,
    Claim,
    EvidenceDigest,
    InvestigationPlan,
    PassageAnalysisInput,
    PlannerInput,
    PlanOp,
    PolicyAnalysis,
    ReportInput,
    ReportNarrative,
    SuspiciousEntity,
    Synthesis,
    SynthesisInput,
)

GROUND_RULES = """You assist a bank's fraud investigation team.
Rules you must follow:
- Use ONLY the evidence in the input. Do not invent transactions, accounts, relationships, amounts or scores.
- Every claim must cite one or more evidence refs (format E-xxxxxxxxxx) that appear in the input.
- A shared device or IP address is an observed relationship, not proof of fraud. Weigh legitimate explanations
  (households, carrier NAT, public wifi, device changes, cleared prior cases).
- Phrase interpretations as hypotheses. Only human reviewers confirm fraud.
- If evidence is insufficient, say what is missing instead of guessing."""

ALL_OPS = [op.value for op in PlanOp]
DEFAULT_PLANS: dict[str, list[PlanOp]] = {
    "account_takeover": [
        PlanOp.TRANSACTION_ANALYSIS,
        PlanOp.BENEFICIARY_NETWORK,
        PlanOp.SHARED_DEVICES,
        PlanOp.SHARED_IPS,
        PlanOp.PRIOR_CASES,
        PlanOp.HISTORICAL_CASES,
        PlanOp.POLICY_LOOKUP,
    ],
    "shared_device_cluster": [
        PlanOp.SHARED_DEVICES,
        PlanOp.SHARED_IPS,
        PlanOp.TRANSACTION_ANALYSIS,
        PlanOp.BENEFICIARY_NETWORK,
        PlanOp.PRIOR_CASES,
        PlanOp.HISTORICAL_CASES,
        PlanOp.POLICY_LOOKUP,
    ],
    "card_testing": [
        PlanOp.TRANSACTION_ANALYSIS,
        PlanOp.SHARED_DEVICES,
        PlanOp.SHARED_IPS,
        PlanOp.PRIOR_CASES,
        PlanOp.HISTORICAL_CASES,
        PlanOp.POLICY_LOOKUP,
    ],
}
# Checks the planner may add to but never drop (the LLM can't skip due diligence).
MANDATORY_OPS = {PlanOp.TRANSACTION_ANALYSIS, PlanOp.PRIOR_CASES}
_REF = re.compile(r"E-[0-9a-f]{10}")


def default_plan(inp: PlannerInput) -> InvestigationPlan:
    ops = DEFAULT_PLANS.get(inp.alert_type, list(PlanOp))
    if inp.round > 0:
        ops = [PlanOp(o) for o in inp.follow_up_requests if o in ALL_OPS] or [PlanOp.MONEY_TRAIL]
    return InvestigationPlan(
        operations=ops,
        focus=f"Establish whether the {inp.alert_type.replace('_', ' ')} alert reflects coordinated "
        "activity or has a legitimate explanation.",
        case_query=f"{inp.alert_type.replace('_', ' ')}: {inp.summary}",
        policy_query=f"policy requirements for {inp.alert_type.replace('_', ' ')} investigations: {inp.summary}",
        rationale="Deterministic default plan for this alert type.",
    )


def _refs_in(evidence: list[EvidenceDigest]) -> set[str]:
    return {e.ref for e in evidence}


def check_claims(claims: list[Claim], allowed: set[str], where: str) -> list[str]:
    problems = []
    for i, c in enumerate(claims):
        if not c.evidence_refs:
            problems.append(f"{where}[{i}] cites no evidence")
        bad = [r for r in c.evidence_refs if r not in allowed]
        if bad:
            problems.append(f"{where}[{i}] cites unknown refs {bad}")
    return problems


# ------------------------------------------------------------------------------ planner
def _plan_validate(inp: PlannerInput, out: InvestigationPlan) -> list[str]:
    return [] if out.operations else ["operations must not be empty"]


PLANNER: LLMTask[PlannerInput, InvestigationPlan] = LLMTask(
    name="planner",
    output_schema=InvestigationPlan,
    system=GROUND_RULES + "\nYou plan which investigative operations to run. Choose only from the allowed list. "
    "In follow-up rounds, plan only what is needed to fill the gaps that were requested.",
    render=lambda i: f"Allowed operations: {', '.join(ALL_OPS)}\nAlert and context:\n{as_json(i.model_dump())}",
    fake=default_plan,
    validate=_plan_validate,
)


# ------------------------------------------------------------------------------ case analysis
def _case_fake(inp: PassageAnalysisInput) -> CaseAnalysis:
    sims, diffs = [], []
    seen: set[str] = set()
    for p in inp.passages:
        text = (p.text or p.summary).lower()
        case = re.search(r"fc-\d{4}-\d+", p.summary.lower())
        name = case.group(0).upper() if case else "A prior case"
        if name in seen:  # several passages of the same case -> one claim citing the first
            continue
        seen.add(name)
        if "cleared" in text or "legitimate" in text:
            diffs.append(
                Claim(
                    statement=f"{name} was closed as cleared after a legitimate explanation was found; "
                    "the current alert should be checked for the same explanation.",
                    evidence_refs=[p.ref],
                )
            )
        else:
            sims.append(
                Claim(
                    statement=f"{name} describes a pattern that may resemble the current alert.", evidence_refs=[p.ref]
                )
            )
    overall = (
        f"{len(sims)} similar and {len(diffs)} contrasting historical passages were found."
        if inp.passages
        else "No historical cases were retrieved."
    )
    return CaseAnalysis(similarities=sims[:4], differences=diffs[:3], overall=overall)


CASE_ANALYSIS: LLMTask[PassageAnalysisInput, CaseAnalysis] = LLMTask(
    name="case_analysis",
    output_schema=CaseAnalysis,
    system=GROUND_RULES + "\nCompare the current alert with the retrieved historical case passages. List concrete "
    "similarities and differences, each citing the passage refs. Note cleared outcomes.",
    render=lambda i: as_json(i.model_dump()),
    fake=_case_fake,
    validate=lambda i, o: (
        check_claims(o.similarities, _refs_in(i.passages), "similarities")
        + check_claims(o.differences, _refs_in(i.passages), "differences")
    ),
)


# ------------------------------------------------------------------------------ policy analysis
def _policy_fake(inp: PassageAnalysisInput) -> PolicyAnalysis:
    reqs = []
    for p in inp.passages[:4]:
        text = re.sub(r"(^|\n)\s*(?:[-*]|\d+\.)\s+", " ", p.text or p.summary)  # drop list markers
        sentences = [x.strip() for x in re.split(r"(?<=[.!?])\s+", " ".join(text.split())) if len(x.strip()) > 25]
        if sentences:
            reqs.append(Claim(statement=f"Applicable guidance: {sentences[0][:220]}", evidence_refs=[p.ref]))
    return PolicyAnalysis(
        requirements=reqs,
        overall="Requirements identified from retrieved guidance." if reqs else "No applicable guidance was retrieved.",
    )


POLICY_ANALYSIS: LLMTask[PassageAnalysisInput, PolicyAnalysis] = LLMTask(
    name="policy_analysis",
    output_schema=PolicyAnalysis,
    system=GROUND_RULES + "\nIdentify the policy and procedure requirements that apply to this investigation, each "
    "citing the passage that states it.",
    render=lambda i: as_json(i.model_dump()),
    fake=_policy_fake,
    validate=lambda i, o: check_claims(o.requirements, _refs_in(i.passages), "requirements"),
)


# ------------------------------------------------------------------------------ synthesis
_SUPPORTING_KINDS = {
    "pass_through": "Funds were forwarded onward shortly after receipt",
    "structuring": "Cash deposits cluster just below the reporting threshold",
    "new_beneficiary_transfer": "Money was sent to newly registered beneficiaries within a day",
    "beneficiary_cluster": "The new beneficiaries are linked to each other",
    "card_testing": "Card activity matches a card-testing pattern",
    "value_cycle": "Funds return to the account through intermediaries",
}


def _synth_fake(inp: SynthesisInput) -> Synthesis:
    supporting: list[Claim] = []
    contradictions: list[Claim] = []
    by_kind: dict[str, list[EvidenceDigest]] = {}
    for e in inp.evidence:
        by_kind.setdefault(e.kind, []).append(e)
    for kind, text in _SUPPORTING_KINDS.items():
        if kind in by_kind:
            supporting.append(
                Claim(
                    statement=f"{text}; this is consistent with coordinated movement of funds.",
                    evidence_refs=[e.ref for e in by_kind[kind][:3]],
                )
            )
    risky_dev = [e for e in by_kind.get("shared_device", []) if not e.benign_context]
    if risky_dev:
        supporting.append(
            Claim(
                statement="A device is shared with customers outside the household. On its own this "
                "is a relationship, not proof.",
                evidence_refs=[e.ref for e in risky_dev[:3]],
            )
        )
    risky_ip = [e for e in by_kind.get("shared_ip", []) if not e.benign_context]
    if risky_ip:
        supporting.append(
            Claim(
                statement="A VPN or datacenter IP is shared with other customers.",
                evidence_refs=[e.ref for e in risky_ip[:3]],
            )
        )
    confirmed = [e for e in by_kind.get("prior_case", []) if "confirmed fraud" in e.summary]
    if confirmed:
        supporting.append(
            Claim(
                statement="An account in the network appeared in a confirmed fraud case.",
                evidence_refs=[e.ref for e in confirmed[:3]],
            )
        )
    benign = [e for e in inp.evidence if e.benign_context]
    if benign:
        contradictions.append(
            Claim(
                statement="Some shared identifiers have legitimate explanations (same household "
                "or shared network infrastructure).",
                evidence_refs=[e.ref for e in benign[:4]],
            )
        )
    cleared = [e for e in by_kind.get("prior_case", []) if "cleared" in e.summary]
    if cleared:
        contradictions.append(
            Claim(
                statement="A prior alert involving this network was cleared.",
                evidence_refs=[e.ref for e in cleared[:3]],
            )
        )
    inconclusive = [e for e in by_kind.get("prior_case", []) if "inconclusive" in e.summary]
    if inconclusive:
        contradictions.append(
            Claim(
                statement="A prior investigation of a linked account was inconclusive, so it "
                "cannot be treated as confirmed involvement.",
                evidence_refs=[e.ref for e in inconclusive[:2]],
            )
        )
    contradictions += [c for c in inp.case_claims if "cleared" in c.statement][:2]
    missing = list(dict.fromkeys(inp.notes))
    follow: list[PlanOp] = []
    if inp.round < inp.max_rounds and "money_trail" not in inp.executed_operations and supporting:
        follow.append(PlanOp.MONEY_TRAIL)
        missing.append("Onward money trail not yet traced")
    narrative = (
        f"{len(supporting)} supporting and {len(contradictions)} contradicting points were identified "
        f"from {len(inp.evidence)} evidence items."
    )
    return Synthesis(
        supporting=supporting,
        contradictions=contradictions,
        missing_information=missing,
        follow_up_operations=follow,
        narrative=narrative,
    )


def _synth_validate(inp: SynthesisInput, out: Synthesis) -> list[str]:
    allowed = _refs_in(inp.evidence) | {r for c in inp.case_claims + inp.policy_claims for r in c.evidence_refs}
    return check_claims(out.supporting, allowed, "supporting") + check_claims(
        out.contradictions, allowed, "contradictions"
    )


SYNTHESIS: LLMTask[SynthesisInput, Synthesis] = LLMTask(
    name="synthesizer",
    output_schema=Synthesis,
    system=GROUND_RULES + "\nCorrelate the findings. Separate supporting evidence, contradicting evidence (legitimate "
    "explanations) and missing information. Request follow-up operations only if a specific gap "
    "would change the conclusion.",
    render=lambda i: as_json(i.model_dump()),
    fake=_synth_fake,
    validate=_synth_validate,
)


# ------------------------------------------------------------------------------ report
def _report_fake(inp: ReportInput) -> ReportNarrative:
    s = inp.synthesis
    graph_kinds = {"shared_device", "shared_ip", "beneficiary", "money_flow", "prior_case", "beneficiary_cluster"}
    graph_claims = [Claim(statement=e.summary, evidence_refs=[e.ref]) for e in inp.evidence if e.kind in graph_kinds][
        :8
    ]
    txn_claims = [
        Claim(statement=e.summary, evidence_refs=[e.ref])
        for e in inp.evidence
        if e.kind
        in {"pass_through", "structuring", "card_testing", "flow_summary", "value_cycle", "new_beneficiary_transfer"}
    ][:8]
    accounts: dict[str, list[str]] = {}
    for e in inp.evidence:
        if e.kind in {"beneficiary", "beneficiary_cluster", "money_flow"}:
            for acct in re.findall(r"A\d{6}", e.summary):
                accounts.setdefault(acct, []).append(e.ref)
    entities = [
        SuspiciousEntity(
            account_id=a, role_hypothesis="linked to the alert subject; role unconfirmed", evidence_refs=refs[:3]
        )
        for a, refs in list(accounts.items())[:6]
    ]
    steps = (
        [
            "Escalate per the escalation procedure and consider a suspicious activity report",
            "Restrict outbound payments on linked accounts pending review",
        ]
        if inp.risk_category in ("high", "critical") and inp.review_decision == "approved"
        else ["Close the alert with the documented legitimate explanation"]
        if inp.review_decision == "rejected"
        else ["Continue monitoring the linked accounts"]
    )
    return ReportNarrative(
        investigation_summary=f"Alert {inp.alert.get('external_ref', '')} ({inp.alert.get('alert_type', '')}) on "
        f"{inp.alert.get('subject_account_id', '')}. {s.narrative} Human review decision: "
        f"{inp.review_decision}.",
        suspicious_entities=entities,
        transaction_patterns=txn_claims,
        graph_relationships=graph_claims,
        historical_case_similarities=inp.case_analysis[:4],
        supporting_evidence=s.supporting,
        contradictions=s.contradictions,
        missing_information=s.missing_information,
        recommended_next_steps=steps,
    )


def _report_validate(inp: ReportInput, out: ReportNarrative) -> list[str]:
    allowed = _refs_in(inp.evidence) | {r for c in inp.case_analysis + inp.policy_requirements for r in c.evidence_refs}
    problems = []
    for name in (
        "transaction_patterns",
        "graph_relationships",
        "historical_case_similarities",
        "supporting_evidence",
        "contradictions",
    ):
        problems += check_claims(getattr(out, name), allowed, name)
    for i, ent in enumerate(out.suspicious_entities):
        if not set(ent.evidence_refs) <= allowed or not ent.evidence_refs:
            problems.append(f"suspicious_entities[{i}] must cite known refs")
    stray = set(_REF.findall(out.investigation_summary)) - allowed
    if stray:
        problems.append(f"summary mentions unknown refs {sorted(stray)}")
    return problems


REPORT: LLMTask[ReportInput, ReportNarrative] = LLMTask(
    name="report_generator",
    output_schema=ReportNarrative,
    system=GROUND_RULES + "\nWrite the investigation report sections. The risk score and factors are computed by "
    "deterministic rules and are given to you; do not change or restate different numbers. "
    "Reflect the human reviewer's decision.",
    render=lambda i: as_json(i.model_dump()),
    fake=_report_fake,
    validate=_report_validate,
)
