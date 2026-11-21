from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import AlertStatus, AlertType, Severity
from app.infrastructure.database.base import Base, SoftDelete, Timestamps, UUIDPrimaryKey, str_enum


class Alert(UUIDPrimaryKey, Timestamps, SoftDelete, Base):
    """A monitoring alert. References graph entities by their graph IDs only (D-005)."""

    __tablename__ = "alerts"
    __table_args__ = (
        Index("ix_alerts_status_created_at", "status", "created_at"),
        Index("ix_alerts_subject_account_id", "subject_account_id"),
    )

    # idempotency for upstream monitoring systems: the same source alert is never ingested twice
    external_ref: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    alert_type: Mapped[AlertType] = mapped_column(str_enum(AlertType, "alert_type"), nullable=False)
    severity: Mapped[Severity] = mapped_column(str_enum(Severity, "severity"), nullable=False)
    status: Mapped[AlertStatus] = mapped_column(
        str_enum(AlertStatus, "alert_status"), nullable=False, server_default=AlertStatus.OPEN.value
    )
    subject_account_id: Mapped[str] = mapped_column(String(64), nullable=False)
    subject_customer_id: Mapped[str | None] = mapped_column(String(64))
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    # triggering transaction IDs, rule IDs etc. -- references, never full transaction payloads
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
