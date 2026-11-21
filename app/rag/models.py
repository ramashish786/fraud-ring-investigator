from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.core.enums import Classification, DocumentType

DocGroup = Literal["case", "guidance"]


def doc_group(doc_type: DocumentType) -> DocGroup:
    return "case" if doc_type == DocumentType.CASE_REPORT else "guidance"


class ChunkRecord(BaseModel):
    chunk_id: str
    document_id: str
    title: str
    doc_type: DocumentType
    classification: Classification
    case_id: str | None = None
    page: int = 1
    section: str | None = None
    position: int
    text: str
    mentions: list[str] = Field(default_factory=list)  # account ids found in the text
    embedding: list[float] | None = None

    @property
    def doc_group(self) -> DocGroup:
        return doc_group(self.doc_type)


class RetrievalFilters(BaseModel):
    max_classification: Classification = Classification.INTERNAL  # caller's clearance (D-016, D-033)
    doc_group: DocGroup | None = None


class Citation(BaseModel):
    """Every retrieved passage keeps its source reference (spec §7)."""

    document_id: str
    chunk_id: str
    title: str
    doc_type: DocumentType
    classification: Classification
    case_id: str | None = None
    page: int
    section: str | None = None


class RetrievedChunk(BaseModel):
    citation: Citation
    text: str
    score: float
    retrievers: list[str]  # which retrievers found it: vector, keyword
