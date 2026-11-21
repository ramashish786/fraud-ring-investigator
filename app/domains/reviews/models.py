from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import ReviewDecision
from app.infrastructure.database.base import Base, UUIDPrimaryKey, str_enum


class Review(UUIDPrimaryKey, Base):
    """Human review decisions. Immutable; a changed mind is a new review row."""

    __tablename__ = "reviews"
    __table_args__ = (Index("ix_reviews_investigation_created", "investigation_id", "created_at"),)

    investigation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False
    )
    reviewer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    decision: Mapped[ReviewDecision] = mapped_column(str_enum(ReviewDecision, "review_decision"), nullable=False)
    comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
