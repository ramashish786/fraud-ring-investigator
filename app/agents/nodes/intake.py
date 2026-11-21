"""1. Alert intake (deterministic): load the alert and the initiator's clearance; record the alert as a fact."""

from __future__ import annotations

import uuid
from typing import Any

from app.core.enums import AssertionLevel, EvidenceSource
from app.domains.alerts.repository import AlertRepository
from app.domains.evidence.schemas import EvidenceItem
from app.domains.identity.repository import UserRepository
from app.domains.investigations.repository import InvestigationRepository

from ..deps import WorkflowDeps
from ..state import InvestigationState


async def intake(state: InvestigationState, deps: WorkflowDeps) -> dict[str, Any]:
    inv_id = uuid.UUID(state["investigation_id"])
    async with deps.db.read_session() as s:
        inv = await InvestigationRepository(s).get(inv_id)
        if inv is None:
            raise LookupError(f"investigation {inv_id} not found")
        alert = await AlertRepository(s).get(inv.alert_id)
        user = await UserRepository(s).get(inv.created_by)
        if alert is None or user is None:
            raise LookupError("alert or initiating user missing")
    item = EvidenceItem(
        kind="alert",
        assertion_level=AssertionLevel.VERIFIED_FACT,
        source=EvidenceSource.POSTGRES,
        source_ref={"alert_id": str(alert.id), "external_ref": alert.external_ref},
        snapshot={
            "alert_type": str(alert.alert_type),
            "severity": str(alert.severity),
            "subject_account_id": alert.subject_account_id,
            "summary": alert.summary,
            "triggering_txn_ids": alert.payload.get("triggering_txn_ids", []),
        },
        summary=f"Alert {alert.external_ref} ({alert.alert_type}, {alert.severity}) on {alert.subject_account_id}: "
        f"{alert.summary}",
    )
    refs = await deps.save_evidence(inv_id, [item], "intake")
    return {
        "alert_id": str(alert.id),
        "round": 0,
        "status": "running",
        "clearance": str(user.clearance),
        "alert": {
            "external_ref": alert.external_ref,
            "alert_type": str(alert.alert_type),
            "severity": str(alert.severity),
            "summary": alert.summary,
            "subject_account_id": alert.subject_account_id,
        },
        "evidence_refs": refs,
        "network_accounts": [alert.subject_account_id],
    }
