"""Signals: structured, numeric observations the deterministic risk engine consumes (D-029).

Evidence is for humans and citations; signals are for rules. Every signal keeps the
evidence refs that justify it, so each risk factor can cite its evidence.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.domains.evidence.schemas import EvidenceItem


class Signal(BaseModel):
    name: str
    value: float
    evidence_refs: list[str] = Field(default_factory=list)
    detail: dict[str, Any] = Field(default_factory=dict)


class AnalysisResult(BaseModel):
    evidence: list[EvidenceItem] = Field(default_factory=list)
    signals: dict[str, Signal] = Field(default_factory=dict)
    network_accounts: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)  # missing / partial information

    def add(self, item: EvidenceItem) -> str:
        self.evidence.append(item)
        return item.ref

    def signal(self, name: str, value: float, refs: list[str] | None = None, **detail: Any) -> None:
        self.signals[name] = Signal(
            name=name, value=value, evidence_refs=list(dict.fromkeys(refs or [])), detail=detail
        )
