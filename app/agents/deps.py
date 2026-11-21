"""Everything nodes need, injected once when the graph is built (no globals)."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, TypeVar

from sqlalchemy import text

from app.core.enums import AssertionLevel
from app.core.settings import Settings
from app.domains.evidence.models import Evidence
from app.domains.evidence.repository import EvidenceRepository
from app.domains.evidence.schemas import EvidenceItem, FindingIn
from app.domains.investigations.repository import InvestigationRepository
from app.domains.risk.engine import RiskEngine
from app.graph.repository import GraphRepository
from app.infrastructure.database.engine import Database
from app.infrastructure.llm.service import LLMService
from app.rag.retrieval.hybrid import HybridSearch

from .prompts.schemas import EvidenceDigest

T = TypeVar("T")


@dataclass
class FaultInjector:
    """Test/evaluation hook: fail a node once per investigation to prove recovery (spec §14)."""

    fail_once: set[str] = field(default_factory=set)
    _fired: set[tuple[str, str]] = field(default_factory=set)

    def maybe_fail(self, node: str, investigation_id: str) -> None:
        key = (node, investigation_id)
        if node in self.fail_once and key not in self._fired:
            self._fired.add(key)
            raise RuntimeError(f"injected fault in {node}")


@dataclass
class WorkflowDeps:
    settings: Settings
    db: Database
    graph: GraphRepository
    search: HybridSearch
    llm: LLMService
    risk: RiskEngine
    faults: FaultInjector = field(default_factory=FaultInjector)

    async def save_evidence(self, inv: uuid.UUID, items: list[EvidenceItem], node: str) -> list[str]:
        async with self.db.unit_of_work() as s:
            return await EvidenceRepository(s).add_many(inv, items, node)

    async def save_findings(self, inv: uuid.UUID, node: str, findings: list[FindingIn]) -> None:
        async with self.db.unit_of_work() as s:
            await EvidenceRepository(s).replace_findings(inv, node, findings)

    async def digest(
        self, inv: uuid.UUID, refs: list[str] | None = None, with_text: bool = False
    ) -> list[EvidenceDigest]:
        async with self.db.read_session() as s:
            rows, _ = await EvidenceRepository(s).list_page(inv, refs=refs, limit=1000)
        return [_digest(e, with_text) for e in rows]

    async def event(
        self,
        inv: uuid.UUID,
        node: str,
        status: str,
        *,
        round_: int = 0,
        duration_ms: int | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        async with self.db.unit_of_work() as s:
            await InvestigationRepository(s).add_event(
                inv, node, status, round_=round_, duration_ms=duration_ms, detail=detail
            )
            if status == "started":  # raw UPDATE: doesn't bump the optimistic-lock version
                await s.execute(
                    text("UPDATE investigations SET current_node = :n WHERE id = :i"), {"n": node, "i": inv}
                )


def _digest(e: Evidence, with_text: bool) -> EvidenceDigest:
    return EvidenceDigest(
        ref=e.ref,
        kind=e.kind,
        level=str(AssertionLevel(e.assertion_level)),
        summary=e.summary,
        benign_context=e.snapshot.get("benign_context"),
        text=e.snapshot.get("text") if with_text else None,
    )


async def with_retry(fn: Callable[[], Awaitable[T]], attempts: int = 3, base_delay: float = 0.5) -> T:
    """Retry transient failures inside a node before degrading (Neo4j blips, timeouts)."""
    for i in range(attempts):
        try:
            return await fn()
        except asyncio.CancelledError:
            raise
        except Exception:
            if i == attempts - 1:
                raise
            await asyncio.sleep(base_delay * 2**i)
    raise AssertionError("unreachable")
