"""Deterministic, versioned risk scoring (D-013, spec §10)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from app.core.enums import Severity
from app.domains.analysis.signals import Signal
from app.domains.investigations.schemas import RiskFactor

DEFAULT_RULES = Path(__file__).resolve().parents[3] / "config" / "risk_rules.yaml"


class Rule(BaseModel):
    id: str
    signal: str
    threshold: float
    weight: float
    per_unit: float = 0.0
    max: float | None = None
    unless: list[str] = Field(default_factory=list)
    description: str


class RiskResult(BaseModel):
    score: int
    category: Severity
    ruleset_version: str
    factors: list[RiskFactor]
    disclaimer: str = "Risk indicators only. Not a determination of fraud."


class RiskEngine:
    def __init__(self, version: str, categories: dict[str, float], rules: list[Rule], mitigations: list[Rule]) -> None:
        self.version = version
        self.categories = sorted(((float(v), Severity(k)) for k, v in categories.items()), key=lambda x: x[0])
        self.rules = rules
        self.mitigations = mitigations

    @classmethod
    def from_yaml(cls, path: Path = DEFAULT_RULES) -> RiskEngine:
        cfg: dict[str, Any] = yaml.safe_load(path.read_text())
        ids = [r["id"] for r in cfg["rules"] + cfg.get("mitigations", [])]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate rule ids in risk rules")
        return cls(
            str(cfg["version"]),
            cfg["categories"],
            [Rule(**r) for r in cfg["rules"]],
            [Rule(**r) for r in cfg.get("mitigations", [])],
        )

    def _contribution(self, rule: Rule, value: float) -> float:
        if value < rule.threshold:
            return 0.0
        w = rule.weight + rule.per_unit * (value - rule.threshold)
        return min(w, rule.max) if rule.max is not None else w

    def assess(self, signals: dict[str, Signal]) -> RiskResult:
        factors: list[RiskFactor] = []
        total = 0.0
        for rule in self.rules:
            sig = signals.get(rule.signal)
            if sig and (w := self._contribution(rule, sig.value)):
                total += w
                factors.append(
                    RiskFactor(
                        rule_id=rule.id,
                        description=rule.description,
                        weight=round(w, 1),
                        evidence_refs=sig.evidence_refs,
                        detail={"signal": rule.signal, "value": sig.value, "threshold": rule.threshold},
                    )
                )
        for m in self.mitigations:
            sig = signals.get(m.signal)
            blocked = any(signals.get(u) and signals[u].value > 0 for u in m.unless)
            if sig and not blocked and sig.value >= m.threshold:
                total += m.weight
                factors.append(
                    RiskFactor(
                        rule_id=m.id,
                        description=m.description,
                        weight=m.weight,
                        evidence_refs=sig.evidence_refs,
                        detail={"signal": m.signal, "value": sig.value, "mitigation": True},
                    )
                )
        score = round(max(0.0, min(100.0, total)))
        category = next(c for lower, c in reversed(self.categories) if score >= lower)
        return RiskResult(score=score, category=category, ruleset_version=self.version, factors=factors)
