from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import ActorType, InvestigationStatus, Severity
from app.infrastructure.database.base import Base, SoftDelete, Timestamps, UUIDPrimaryKey, str_enum

_ACTIVE = "status NOT IN ('completed','failed','cancelled') AND deleted_at IS NULL"


class Investigation(UUIDPrimaryKey, Timestamps, SoftDelete, Base):
    __tablename__ = "investigations"
    __table_args__ = (
        # at most one *active* investigation per alert
        Index("uq_investigations_alert_active", "alert_id", unique=True, postgresql_where=text(_ACTIVE)),
        Index("ix_investigations_status_created_at", "status", "created_at"),
        CheckConstraint("risk_score IS NULL OR risk_score BETWEEN 0 AND 100", name="risk_score_range"),
        CheckConstraint("round >= 0 AND round <= 5", name="round_bounds"),
    )

    alert_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("alerts.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[InvestigationStatus] = mapped_column(
        str_enum(InvestigationStatus, "investigation_status"),
        nullable=False,
        server_default=InvestigationStatus.QUEUED.value,
    )
    # client-supplied Idempotency-Key for POST /investigations (D-007 / Phase 5)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), unique=True)
    current_node: Mapped[str | None] = mapped_column(String(64))
    round: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("0"))
    risk_score: Mapped[int | None] = mapped_column(SmallInteger)
    risk_category: Mapped[Severity | None] = mapped_column(str_enum(Severity, "risk_category"))
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    assigned_to: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # optimistic locking: concurrent status updates (API vs worker) fail loudly instead of overwriting
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))

    __mapper_args__ = {"version_id_col": version}  # noqa: RUF012 - SQLAlchemy reads this mapping


class InvestigationStatusHistory(Base):
    """Append-only status transitions (the timeline)."""

    __tablename__ = "investigation_status_history"
    __table_args__ = (Index("ix_inv_status_history_inv_created", "investigation_id", "created_at"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    investigation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False
    )
    from_status: Mapped[InvestigationStatus | None] = mapped_column(
        str_enum(InvestigationStatus, "history_from_status")
    )
    to_status: Mapped[InvestigationStatus] = mapped_column(
        str_enum(InvestigationStatus, "history_to_status"), nullable=False
    )
    reason: Mapped[str | None] = mapped_column(Text)
    actor_type: Mapped[ActorType] = mapped_column(str_enum(ActorType, "history_actor_type"), nullable=False)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class InvestigationEvent(Base):
    """Workflow node execution log: feeds the timeline API and node-status observability (D-032)."""

    __tablename__ = "investigation_events"
    __table_args__ = (Index("ix_investigation_events_inv_created", "investigation_id", "created_at"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    investigation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False
    )
    node: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # started, completed, failed, interrupted
    round: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("0"))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class RiskAssessment(UUIDPrimaryKey, Base):
    """Deterministic rule output (D-013). One row per assessment run; the latest is current."""

    __tablename__ = "risk_assessments"
    __table_args__ = (
        Index("ix_risk_assessments_inv_created", "investigation_id", "created_at"),
        CheckConstraint("score BETWEEN 0 AND 100", name="score_range"),
    )

    investigation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False
    )
    score: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    category: Mapped[Severity] = mapped_column(str_enum(Severity, "risk_assessment_category"), nullable=False)
    ruleset_version: Mapped[str] = mapped_column(String(32), nullable=False)
    factors: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
