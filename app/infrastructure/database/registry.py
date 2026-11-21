"""Imports every ORM model so `Base.metadata` is complete (used by Alembic and tests)."""

from app.domains.alerts.models import Alert
from app.domains.audit.models import AuditLog
from app.domains.documents.models import Document
from app.domains.evidence.models import Evidence, Finding, FindingEvidence
from app.domains.identity.models import RoleModel, User, UserRole
from app.domains.investigations.models import (
    Investigation,
    InvestigationEvent,
    InvestigationStatusHistory,
    RiskAssessment,
)
from app.domains.reports.models import Report
from app.domains.reviews.models import Review
from app.infrastructure.database.base import Base
from app.infrastructure.jobs.models import Job
from app.infrastructure.llm.models import LLMCall

__all__ = [
    "Alert",
    "AuditLog",
    "Base",
    "Document",
    "Evidence",
    "Finding",
    "FindingEvidence",
    "Investigation",
    "InvestigationEvent",
    "InvestigationStatusHistory",
    "Job",
    "LLMCall",
    "Report",
    "Review",
    "RiskAssessment",
    "RoleModel",
    "User",
    "UserRole",
]
