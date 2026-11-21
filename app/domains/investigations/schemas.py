from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import InvestigationStatus, ReviewDecision, Severity
from app.domains.alerts.schemas import AlertRead


class InvestigationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    alert_id: uuid.UUID


class RiskFactor(BaseModel):
    rule_id: str
    description: str
    weight: float
    evidence_refs: list[str]
    detail: dict[str, Any] = Field(default_factory=dict)


class RiskAssessmentRead(BaseModel):
    score: int
    category: Severity
    ruleset_version: str
    factors: list[RiskFactor]
    created_at: datetime
    disclaimer: str = "Risk indicators, not a determination of fraud. Confirmed outcomes come only from human review."


class InvestigationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    alert_id: uuid.UUID
    status: InvestigationStatus
    current_node: str | None
    round: int
    risk_score: int | None
    risk_category: Severity | None
    created_by: uuid.UUID
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class InvestigationDetail(InvestigationRead):
    alert: AlertRead
    risk: RiskAssessmentRead | None = None
    status_url: str | None = None


class InvestigationAccepted(BaseModel):
    investigation_id: uuid.UUID
    status: InvestigationStatus
    status_url: str


class ReviewCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: ReviewDecision
    comment: str | None = Field(default=None, max_length=4000)


class ReviewRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    decision: ReviewDecision
    comment: str | None
    reviewer_id: uuid.UUID
    created_at: datetime


class TimelineEntry(BaseModel):
    ts: datetime
    kind: Literal["status", "node", "review"]
    label: str
    detail: dict[str, Any] = Field(default_factory=dict)


class ReportRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    investigation_id: uuid.UUID
    version: int
    content: dict[str, Any]
    markdown: str
    ruleset_version: str | None
    generated_by_model: str | None
    created_at: datetime
