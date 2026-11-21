from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import Classification, DocumentStatus, DocumentType


class DocumentMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=300)
    doc_type: DocumentType
    classification: Classification
    source: str = Field(default="upload", min_length=1, max_length=200)
    case_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,64}$")


class DocumentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    title: str
    doc_type: DocumentType
    classification: Classification
    source: str
    case_id: str | None
    mime_type: str
    size_bytes: int
    status: DocumentStatus
    chunk_count: int
    error: str | None
    created_at: datetime
