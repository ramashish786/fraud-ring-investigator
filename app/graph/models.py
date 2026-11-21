"""Typed results returned by graph repositories.

Driver/record types never leave the repository layer: callers get these models, each
carrying enough provenance (source_ref, first/last seen, observation counts) to be
turned into evidence (D-006, D-018).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

MAX_DEPTH = 4
MAX_LIMIT = 500
MAX_USERS_PER_IDENTIFIER = 25
SHARED_INFRA_IP_KINDS = frozenset({"mobile_cgnat", "public_wifi", "corporate"})
RISKY_IP_KINDS = frozenset({"vpn", "datacenter"})


def clamp_depth(depth: int) -> int:
    return max(1, min(MAX_DEPTH, depth))


def clamp_limit(limit: int) -> int:
    return max(1, min(MAX_LIMIT, limit))


class AccountSummary(BaseModel):
    account_id: str
    customer_id: str
    customer_kind: str | None = None
    account_type: str | None = None
    opened_at: datetime | None = None
    customer_created_at: datetime | None = None
    address_key: str | None = None
    city: str | None = None


class TxnRecord(BaseModel):
    txn_id: str
    ts: datetime
    amount: float
    channel: str
    status: str
    src_account: str | None = None
    dst_account: str | None = None
    merchant_id: str | None = None
    device_id: str | None = None
    ip: str | None = None


class PaidEdge(BaseModel):
    """Aggregated money flow between two accounts (derived from transactions, D-002)."""

    src: str
    dst: str
    count: int
    total: float
    first_ts: datetime
    last_ts: datetime
    hop: int = Field(ge=1, le=MAX_DEPTH)


class IdentifierUse(BaseModel):
    customer_id: str
    account_ids: list[str] = Field(default_factory=list)
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    observations: int = 0
    address_key: str | None = None
    source_ref: str | None = None


class SharedDevice(BaseModel):
    device_id: str
    emulator: bool = False
    os: str | None = None
    user_count: int
    users: list[IdentifierUse]
    ip_kinds: list[str] = Field(default_factory=list)


class SharedIP(BaseModel):
    ip: str
    kind: str
    country: str | None = None
    user_count: int
    users: list[IdentifierUse]

    @property
    def is_shared_infrastructure(self) -> bool:
        return self.kind in SHARED_INFRA_IP_KINDS


class BeneficiaryLink(BaseModel):
    beneficiary_id: str
    owner_account: str
    target_account: str
    target_customer_id: str | None = None
    added_at: datetime
    device_id: str | None = None
    ip: str | None = None
    source: str | None = None


class CaseLink(BaseModel):
    case_id: str
    typology: str
    status: Literal["confirmed_fraud", "inconclusive", "cleared", "open"]
    role: str | None = None
    account_id: str
    closed_at: datetime | None = None
    summary: str | None = None


class GraphNode(BaseModel):
    id: str
    label: str  # Account, Customer, Device, IPAddress, FraudCase
    properties: dict[str, str | int | float | bool | None] = Field(default_factory=dict)


class GraphEdge(BaseModel):
    source: str
    target: str
    type: str  # OWNS, USES, LOGGED_IN_FROM, PAID, ADDED_BENEFICIARY, INVOLVED_IN
    properties: dict[str, str | int | float | bool | None] = Field(default_factory=dict)


class Subgraph(BaseModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    truncated: bool = False
