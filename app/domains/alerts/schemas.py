from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.enums import AlertStatus, AlertType, Severity

_ID = r"^[A-Za-z0-9_-]{1,64}$"


class AlertCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    external_ref: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    alert_type: AlertType
    severity: Severity
    subject_account_id: str = Field(pattern=_ID)
    subject_customer_id: str | None = Field(default=None, pattern=_ID)
    summary: str = Field(min_length=1, max_length=2000)
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("payload")
    @classmethod
    def _payload_is_references(cls, v: dict[str, Any]) -> dict[str, Any]:
        # Alerts carry references (txn ids, rule ids), never full records: keep payloads small.
        if len(str(v)) > 10_000:
            raise ValueError("payload too large; send references, not records")
        return v


class AlertRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    external_ref: str
    alert_type: AlertType
    severity: Severity
    status: AlertStatus
    subject_account_id: str
    subject_customer_id: str | None
    summary: str
    payload: dict[str, Any]
    created_at: datetime
