"""Domain enumerations shared by ORM models, API schemas and (later) the workflow.

Stored as VARCHAR + CHECK constraint, not native PostgreSQL enums (D-022).
"""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    ANALYST = "analyst"
    REVIEWER = "reviewer"
    AUDITOR = "auditor"
    ADMIN = "admin"


class Classification(StrEnum):
    """Document / user clearance levels, ordered from least to most sensitive."""

    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"

    @property
    def rank(self) -> int:
        return list(Classification).index(self)


class AlertType(StrEnum):
    RAPID_PASS_THROUGH = "rapid_pass_through"  # noqa: S105 - not a password
    STRUCTURING = "structuring"
    ACCOUNT_TAKEOVER = "account_takeover"
    SHARED_DEVICE_CLUSTER = "shared_device_cluster"
    BENEFICIARY_FAN_IN = "beneficiary_fan_in"
    CARD_TESTING = "card_testing"
    MANUAL = "manual"


class Severity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class AlertStatus(StrEnum):
    OPEN = "open"
    UNDER_INVESTIGATION = "under_investigation"
    CLOSED = "closed"


class InvestigationStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    AWAITING_REVIEW = "awaiting_review"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @classmethod
    def terminal(cls) -> frozenset[InvestigationStatus]:
        return frozenset({cls.COMPLETED, cls.FAILED, cls.CANCELLED})


class AssertionLevel(StrEnum):
    """D-006: who is allowed to claim what. LLM nodes may only emit HYPOTHESIS."""

    VERIFIED_FACT = "verified_fact"
    OBSERVED_RELATIONSHIP = "observed_relationship"
    INFERRED_PATTERN = "inferred_pattern"
    HYPOTHESIS = "hypothesis"
    CONFIRMED_OUTCOME = "confirmed_outcome"


class EvidenceSource(StrEnum):
    NEO4J = "neo4j"
    POSTGRES = "postgres"
    DOCUMENT = "document"
    RULE_ENGINE = "rule_engine"
    HUMAN = "human"
    LLM = "llm"


class ReviewDecision(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"
    NEEDS_MORE_WORK = "needs_more_work"


class DocumentType(StrEnum):
    CASE_REPORT = "case_report"
    PROCEDURE = "procedure"
    POLICY = "policy"
    MONITORING_GUIDELINE = "monitoring_guideline"
    FRAUD_PATTERN = "fraud_pattern"


class DocumentStatus(StrEnum):
    UPLOADED = "uploaded"
    PROCESSING = "processing"
    INDEXED = "indexed"
    FAILED = "failed"


class JobKind(StrEnum):
    START_INVESTIGATION = "start_investigation"
    RESUME_INVESTIGATION = "resume_investigation"
    INGEST_DOCUMENT = "ingest_document"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"  # will retry (attempts < max_attempts)
    DEAD = "dead"  # gave up; needs a human


class ActorType(StrEnum):
    USER = "user"
    SYSTEM = "system"
