"""Graph repository port (D-026).

Two implementations satisfy this protocol:
* `Neo4jGraphRepository`: production; runs only catalog queries (D-008).
* `InMemoryGraphRepository`: built from the same simulator export; used by unit/e2e tests
  and the offline evaluation where Neo4j is unavailable.

`tests/contract/test_graph_repository_contract.py` runs the same assertions against both,
so behaviour can't silently diverge.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Protocol

from .models import AccountSummary, BeneficiaryLink, CaseLink, PaidEdge, SharedDevice, SharedIP, TxnRecord


class GraphRepository(Protocol):
    async def account_summaries(self, account_ids: list[str]) -> list[AccountSummary]: ...

    async def transactions(
        self, account_id: str, *, since: datetime | None = None, until: datetime | None = None, limit: int = 200
    ) -> list[TxnRecord]:
        """Transactions sent or received by the account, oldest first."""
        ...

    async def money_trail(
        self,
        account_id: str,
        *,
        direction: Literal["out", "in"],
        depth: int = 2,
        min_amount: float = 0.0,
        limit: int = 200,
    ) -> list[PaidEdge]:
        """Aggregated PAID edges reachable within `depth` hops (depth clamped to 1..4)."""
        ...

    async def shared_devices(self, account_ids: list[str], *, limit: int = 50) -> list[SharedDevice]:
        """Devices used by the owners of these accounts *and* by at least one other customer."""
        ...

    async def shared_ips(self, account_ids: list[str], *, limit: int = 50) -> list[SharedIP]:
        """IPs used by the owners of these accounts and by at least one other customer."""
        ...

    async def beneficiaries(self, account_ids: list[str], *, limit: int = 100) -> list[BeneficiaryLink]:
        """Beneficiaries registered by these accounts, newest first."""
        ...

    async def beneficiary_fan_in(self, target_accounts: list[str]) -> dict[str, int]:
        """For each target account: how many distinct accounts registered it as a beneficiary."""
        ...

    async def prior_cases(self, account_ids: list[str]) -> list[CaseLink]: ...

    async def upsert_investigation(self, investigation_id: str, status: str, account_ids: list[str]) -> None:
        """Thin Investigation mirror in the graph (D-005). Idempotent."""
        ...
