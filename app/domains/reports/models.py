from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import Base, UUIDPrimaryKey


class Report(UUIDPrimaryKey, Base):
    """Versioned investigation reports; regenerating creates version n+1, never overwrites."""

    __tablename__ = "reports"
    __table_args__ = (
        UniqueConstraint("investigation_id", "version", name="uq_reports_investigation_version"),
        CheckConstraint("version >= 1", name="version_positive"),
    )

    investigation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)  # structured report (Pydantic schema)
    markdown: Mapped[str] = mapped_column(Text, nullable=False)
    ruleset_version: Mapped[str | None] = mapped_column(String(32))
    generated_by_model: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
