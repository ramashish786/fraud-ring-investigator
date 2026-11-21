"""Evidence and findings (D-005, D-006, D-012).

Evidence rows are immutable: they hold the graph/document identifiers *and a snapshot*
of the values the investigation relied on, so a report stays reproducible after the
graph is reloaded. `content_hash` makes writes idempotent when a node is re-run after
a crash or a resume.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import AssertionLevel, EvidenceSource
from app.infrastructure.database.base import Base, UUIDPrimaryKey, str_enum


class Evidence(UUIDPrimaryKey, Base):
    __tablename__ = "evidence"
    __table_args__ = (
        UniqueConstraint("investigation_id", "content_hash", name="uq_evidence_investigation_hash"),
        UniqueConstraint("investigation_id", "ref", name="uq_evidence_investigation_ref"),
        Index("ix_evidence_investigation_level", "investigation_id", "assertion_level"),
        CheckConstraint("char_length(content_hash) = 64", name="content_hash_sha256"),
        # D-006: hypotheses come only from the LLM, and the LLM may only produce hypotheses
        CheckConstraint("(source = 'llm') = (assertion_level = 'hypothesis')", name="llm_only_hypothesis"),
    )

    investigation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False
    )
    ref: Mapped[str] = mapped_column(String(16), nullable=False)  # citation handle "E-" + hash prefix (D-028)
    kind: Mapped[str] = mapped_column(String(48), nullable=False)  # transaction, shared_device, rule_hit, chunk, ...
    assertion_level: Mapped[AssertionLevel] = mapped_column(
        str_enum(AssertionLevel, "evidence_assertion_level"), nullable=False
    )
    source: Mapped[EvidenceSource] = mapped_column(str_enum(EvidenceSource, "evidence_source"), nullable=False)
    source_ref: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)  # graph ids / chunk ids / rule id
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)  # values relied on, frozen
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    produced_by_node: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Finding(UUIDPrimaryKey, Base):
    __tablename__ = "findings"
    __table_args__ = (Index("ix_findings_investigation_id", "investigation_id"),)

    investigation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False
    )
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(48), nullable=False)  # supporting, contradiction, missing_info
    assertion_level: Mapped[AssertionLevel] = mapped_column(
        str_enum(AssertionLevel, "finding_assertion_level"), nullable=False
    )
    is_supported: Mapped[bool] = mapped_column(Boolean, nullable=False)  # False -> counts as unsupported claim
    produced_by_node: Mapped[str] = mapped_column(String(64), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))  # order within a node
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class FindingEvidence(Base):
    __tablename__ = "finding_evidence"

    finding_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("findings.id", ondelete="CASCADE"), primary_key=True
    )
    evidence_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("evidence.id", ondelete="CASCADE"), primary_key=True
    )
