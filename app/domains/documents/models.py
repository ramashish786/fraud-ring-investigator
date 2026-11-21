"""Document metadata (Postgres). Chunks + embeddings live in Neo4j (D-005, D-009)."""

from __future__ import annotations

import uuid

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import Classification, DocumentStatus, DocumentType
from app.infrastructure.database.base import Base, SoftDelete, Timestamps, UUIDPrimaryKey, str_enum


class Document(UUIDPrimaryKey, Timestamps, SoftDelete, Base):
    __tablename__ = "documents"
    __table_args__ = (
        # same bytes uploaded twice -> same document (live rows only)
        Index("uq_documents_sha256_live", "sha256", unique=True, postgresql_where=text("deleted_at IS NULL")),
        Index("ix_documents_status", "status"),
        Index("ix_documents_case_id", "case_id"),
        CheckConstraint("size_bytes > 0", name="size_positive"),
        CheckConstraint("chunk_count >= 0", name="chunk_count_non_negative"),
    )

    title: Mapped[str] = mapped_column(String(300), nullable=False)
    doc_type: Mapped[DocumentType] = mapped_column(str_enum(DocumentType, "document_type"), nullable=False)
    source: Mapped[str] = mapped_column(String(200), nullable=False)
    case_id: Mapped[str | None] = mapped_column(String(64))  # historical FraudCase id, if any
    classification: Mapped[Classification] = mapped_column(
        str_enum(Classification, "document_classification"), nullable=False
    )
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[DocumentStatus] = mapped_column(
        str_enum(DocumentStatus, "document_status"), nullable=False, server_default=DocumentStatus.UPLOADED.value
    )
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    error: Mapped[str | None] = mapped_column(Text)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
