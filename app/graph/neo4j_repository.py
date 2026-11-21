"""Production GraphRepository backed by Neo4j. Runs only catalog queries (D-008)."""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any, Literal, LiteralString

from app.infrastructure.neo4j.client import Neo4jClient
from app.infrastructure.observability.logging import get_logger
from app.infrastructure.observability.metrics import GRAPH_QUERY_LATENCY

from . import queries as q
from .models import (
    MAX_USERS_PER_IDENTIFIER,
    AccountSummary,
    BeneficiaryLink,
    CaseLink,
    PaidEdge,
    SharedDevice,
    SharedIP,
    TxnRecord,
    clamp_limit,
)

log = get_logger("graph.neo4j")


class Neo4jGraphRepository:
    def __init__(self, client: Neo4jClient) -> None:
        self._client = client

    async def _read(self, name: str, cypher: LiteralString, params: dict[str, Any]) -> list[dict[str, Any]]:
        start = time.perf_counter()
        try:
            return await self._client.read(cypher, params)
        finally:  # query latency per catalog entry (observability requirement)
            GRAPH_QUERY_LATENCY.labels(name).observe(time.perf_counter() - start)
            log.debug("graph.query", query=name, duration_ms=round((time.perf_counter() - start) * 1000, 1))

    async def account_summaries(self, account_ids: list[str]) -> list[AccountSummary]:
        rows = await self._read("account_summaries", q.ACCOUNT_SUMMARIES, {"account_ids": account_ids})
        return [AccountSummary.model_validate(r) for r in rows]

    async def transactions(
        self, account_id: str, *, since: datetime | None = None, until: datetime | None = None, limit: int = 200
    ) -> list[TxnRecord]:
        rows = await self._read(
            "transactions",
            q.TRANSACTIONS,
            {
                "account_id": account_id,
                "since": since.isoformat() if since else None,
                "until": until.isoformat() if until else None,
                "limit": clamp_limit(limit),
            },
        )
        return [TxnRecord.model_validate(r) for r in rows]

    async def money_trail(
        self,
        account_id: str,
        *,
        direction: Literal["out", "in"],
        depth: int = 2,
        min_amount: float = 0.0,
        limit: int = 200,
    ) -> list[PaidEdge]:
        rows = await self._read(
            f"money_trail_{direction}",
            q.money_trail_query(direction, depth),
            {"account_id": account_id, "min_amount": min_amount, "limit": clamp_limit(limit), "path_limit": 2000},
        )
        return [PaidEdge.model_validate(r) for r in rows]

    async def shared_devices(self, account_ids: list[str], *, limit: int = 50) -> list[SharedDevice]:
        rows = await self._read(
            "shared_devices",
            q.SHARED_DEVICES,
            {"account_ids": account_ids, "limit": clamp_limit(limit), "max_users": MAX_USERS_PER_IDENTIFIER},
        )
        return [SharedDevice.model_validate(r) for r in rows]

    async def shared_ips(self, account_ids: list[str], *, limit: int = 50) -> list[SharedIP]:
        rows = await self._read(
            "shared_ips",
            q.SHARED_IPS,
            {"account_ids": account_ids, "limit": clamp_limit(limit), "max_users": MAX_USERS_PER_IDENTIFIER},
        )
        return [SharedIP.model_validate(r) for r in rows]

    async def beneficiaries(self, account_ids: list[str], *, limit: int = 100) -> list[BeneficiaryLink]:
        rows = await self._read(
            "beneficiaries", q.BENEFICIARIES, {"account_ids": account_ids, "limit": clamp_limit(limit)}
        )
        return [BeneficiaryLink.model_validate(r) for r in rows]

    async def beneficiary_fan_in(self, target_accounts: list[str]) -> dict[str, int]:
        rows = await self._read("beneficiary_fan_in", q.BENEFICIARY_FAN_IN, {"targets": target_accounts})
        return {r["target_account"]: int(r["owners"]) for r in rows}

    async def prior_cases(self, account_ids: list[str]) -> list[CaseLink]:
        rows = await self._read("prior_cases", q.PRIOR_CASES, {"account_ids": account_ids})
        return [CaseLink.model_validate(r) for r in rows]

    async def upsert_investigation(self, investigation_id: str, status: str, account_ids: list[str]) -> None:
        await self._client.write(
            q.UPSERT_INVESTIGATION, {"investigation_id": investigation_id, "status": status, "account_ids": account_ids}
        )
