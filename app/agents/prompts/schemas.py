"""Typed inputs and structured outputs for the five LLM touchpoints (D-011, spec §8)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class PlanOp(StrEnum):
    TRANSACTION_ANALYSIS = "transaction_analysis"
    MONEY_TRAIL = "money_trail"
    SHARED_DEVICES = "shared_devices"
    SHARED_IPS = "shared_ips"
    BENEFICIARY_NETWORK = "beneficiary_network"
    PRIOR_CASES = "prior_cases"
    HISTORICAL_CASES = "historical_cases"
    POLICY_LOOKUP = "policy_lookup"


GRAPH_OPS = {
    PlanOp.MONEY_TRAIL,
    PlanOp.SHARED_DEVICES,
    PlanOp.SHARED_IPS,
    PlanOp.BENEFICIARY_NETWORK,
    PlanOp.PRIOR_CASES,
}


class Claim(BaseModel):
    statement: str = Field(description="One factual-sounding sentence, phrased as a hypothesis where it is one")
    evidence_refs: list[str] = Field(description="Evidence refs (E-xxxxxxxxxx) from the input that support it")


class EvidenceDigest(BaseModel):
    ref: str
    kind: str
    level: str
    summary: str
    benign_context: str | None = None
    text: str | None = None  # passages only


# ---------------------------------------------------------------- 1. planner
class PlannerInput(BaseModel):
    alert_type: str
    severity: str
    summary: str
    round: int
    follow_up_requests: list[str] = Field(default_factory=list)
    already_executed: list[str] = Field(default_factory=list)


class InvestigationPlan(BaseModel):
    operations: list[PlanOp] = Field(description="Operations to run now, chosen only from the allowed list")
    focus: str = Field(description="What this round should establish, in one sentence")
    case_query: str = Field(description="Search query for similar historical cases")
    policy_query: str = Field(description="Search query for applicable policies and procedures")
    rationale: str


# ---------------------------------------------------------------- 2/3. case + policy analysis
class PassageAnalysisInput(BaseModel):
    alert_summary: str
    key_signals: dict[str, float]
    passages: list[EvidenceDigest]


class CaseAnalysis(BaseModel):
    similarities: list[Claim]
    differences: list[Claim]
    overall: str


class PolicyAnalysis(BaseModel):
    requirements: list[Claim] = Field(description="Requirements that apply to this investigation")
    overall: str


# ---------------------------------------------------------------- 4. synthesis
class SynthesisInput(BaseModel):
    alert_type: str
    alert_summary: str
    round: int
    max_rounds: int
    executed_operations: list[str]
    signals: dict[str, float]
    evidence: list[EvidenceDigest]
    notes: list[str]
    case_claims: list[Claim]
    policy_claims: list[Claim]


class Synthesis(BaseModel):
    supporting: list[Claim] = Field(description="Evidence-backed points that support coordinated fraud")
    contradictions: list[Claim] = Field(description="Evidence that points to a legitimate explanation")
    missing_information: list[str]
    follow_up_operations: list[PlanOp] = Field(
        default_factory=list, description="Operations still needed; empty if none"
    )
    narrative: str


# ---------------------------------------------------------------- 5. report
class SuspiciousEntity(BaseModel):
    account_id: str
    role_hypothesis: str
    evidence_refs: list[str]


class ReportInput(BaseModel):
    alert: dict[str, str]
    risk_score: int
    risk_category: str
    risk_factors: list[str]
    review_decision: str
    review_comment: str
    synthesis: Synthesis
    case_analysis: list[Claim]
    policy_requirements: list[Claim]
    evidence: list[EvidenceDigest]


class ReportNarrative(BaseModel):
    """LLM-written sections. Scores, factors and the evidence appendix are added deterministically."""

    investigation_summary: str
    suspicious_entities: list[SuspiciousEntity]
    transaction_patterns: list[Claim]
    graph_relationships: list[Claim]
    historical_case_similarities: list[Claim]
    supporting_evidence: list[Claim]
    contradictions: list[Claim]
    missing_information: list[str]
    recommended_next_steps: list[str]
