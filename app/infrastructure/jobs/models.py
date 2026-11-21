"""Durable job queue table (D-007). Claimed with SELECT ... FOR UPDATE SKIP LOCKED (Phase 4).

A job row is inserted in the same transaction as the business change that needs it
(transactional outbox), so work is never lost between "saved" and "scheduled".
"""

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
    SmallInteger,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import JobKind, JobStatus
from app.infrastructure.database.base import Base, Timestamps, str_enum


class Job(Timestamps, Base):
    __tablename__ = "jobs"
    __table_args__ = (
        # the claim query scans only runnable jobs
        Index("ix_jobs_runnable", "run_after", postgresql_where=text("status IN ('queued','failed')")),
        Index("ix_jobs_investigation_id", "investigation_id"),
        CheckConstraint("attempts >= 0 AND attempts <= max_attempts", name="attempts_bounds"),
        CheckConstraint(
            "(kind IN ('start_investigation','resume_investigation') AND investigation_id IS NOT NULL)"
            " OR (kind = 'ingest_document' AND document_id IS NOT NULL)",
            name="kind_target",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    kind: Mapped[JobKind] = mapped_column(str_enum(JobKind, "job_kind"), nullable=False)
    status: Mapped[JobStatus] = mapped_column(
        str_enum(JobStatus, "job_status"), nullable=False, server_default=JobStatus.QUEUED.value
    )
    investigation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("investigations.id", ondelete="CASCADE")
    )
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE")
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    attempts: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("0"))
    max_attempts: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("5"))
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    leased_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    leased_by: Mapped[str | None] = mapped_column(String(128))
    last_error: Mapped[str | None] = mapped_column(Text)
