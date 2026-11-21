from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.enums import AssertionLevel, Classification, EvidenceSource


class EvidenceItem(BaseModel):
    """Evidence as produced by an analysis step. The producer sets the assertion level (D-006)."""

    model_config = ConfigDict(frozen=True)

    kind: str = Field(max_length=48)
    assertion_level: AssertionLevel
    source: EvidenceSource
    source_ref: dict[str, Any]
    snapshot: dict[str, Any]
    summary: str
    classification: Classification = Classification.INTERNAL

    @model_validator(mode="after")
    def _llm_only_hypothesis(self) -> EvidenceItem:
        if (self.source == EvidenceSource.LLM) != (self.assertion_level == AssertionLevel.HYPOTHESIS):
            raise ValueError("LLM output may only be recorded as a hypothesis, and only LLM output is a hypothesis")
        return self

    @property
    def content_hash(self) -> str:
        body = json.dumps(
            {
                "k": self.kind,
                "l": self.assertion_level.value,
                "s": self.source.value,
                "r": self.source_ref,
                "n": self.snapshot,
            },
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(body.encode()).hexdigest()

    @property
    def ref(self) -> str:
        """Stable citation handle derived from content (D-028): no counters, no races."""
        return "E-" + self.content_hash[:10]


class EvidenceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    ref: str
    kind: str
    assertion_level: AssertionLevel
    source: EvidenceSource
    source_ref: dict[str, Any]
    snapshot: dict[str, Any]
    summary: str
    produced_by_node: str
    created_at: datetime
    redacted: bool = False


class FindingIn(BaseModel):
    statement: str
    category: str  # supporting | contradiction | missing_information | similarity | difference | requirement
    assertion_level: AssertionLevel
    evidence_refs: list[str] = Field(default_factory=list)
    is_supported: bool = True


class FindingRead(BaseModel):
    id: uuid.UUID
    statement: str
    category: str
    assertion_level: AssertionLevel
    is_supported: bool
    produced_by_node: str
    evidence_refs: list[str]
