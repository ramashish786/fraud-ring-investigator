"""Typed workflow state (spec §9).

The state carries *references* (evidence refs, account ids) and small summaries; evidence
bodies, documents and transaction histories live in PostgreSQL/Neo4j. That keeps
checkpoints small and stops every node from receiving huge payloads.

Reducers make parallel branches safe: lists from concurrent investigators are appended
or unioned, signals are merged by name.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict


def union(a: list[str] | None, b: list[str] | None) -> list[str]:
    return list(dict.fromkeys([*(a or []), *(b or [])]))


def merge(a: dict[str, Any] | None, b: dict[str, Any] | None) -> dict[str, Any]:
    return {**(a or {}), **(b or {})}


class InvestigationState(TypedDict, total=False):
    investigation_id: str
    alert_id: str
    alert: dict[str, Any]  # external_ref, alert_type, severity, summary, subject_account_id
    clearance: str  # retrieval clearance of the initiating analyst (D-033)
    round: int
    plan: dict[str, Any]
    follow_up_ops: list[str]
    executed_ops: Annotated[list[str], union]
    transaction_findings: Annotated[list[dict[str, Any]], operator.add]
    graph_findings: Annotated[list[dict[str, Any]], operator.add]
    historical_cases: Annotated[list[dict[str, Any]], operator.add]
    policy_findings: Annotated[list[dict[str, Any]], operator.add]
    signals: Annotated[dict[str, dict[str, Any]], merge]  # name -> Signal dump
    evidence_refs: Annotated[list[str], union]
    network_accounts: Annotated[list[str], union]
    notes: Annotated[list[str], union]
    synthesis: dict[str, Any]
    contradictions: list[dict[str, Any]]
    missing_information: list[str]
    risk: dict[str, Any]
    review: dict[str, Any]
    report_version: int
    status: str
    errors: Annotated[list[dict[str, Any]], operator.add]
