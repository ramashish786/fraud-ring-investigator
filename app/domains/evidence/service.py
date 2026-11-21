"""Evidence access with clearance-based redaction (D-033).

Evidence derived from a document carries the document's classification. A viewer whose
clearance is below it sees that the evidence exists (so the report's citations still
resolve) but not its content.
"""

from __future__ import annotations

import uuid

from app.core.enums import AssertionLevel, Classification
from app.core.pagination import Page
from app.domains.identity.actor import Actor
from app.infrastructure.database.engine import Database

from .models import Evidence
from .repository import EvidenceRepository
from .schemas import EvidenceRead, FindingRead


def redact(e: Evidence, actor: Actor) -> EvidenceRead:
    out = EvidenceRead.model_validate(e)
    level = Classification(e.snapshot.get("classification", Classification.INTERNAL.value))
    if level.rank > actor.clearance.rank:
        out.summary = f"[redacted: requires {level.value} clearance]"
        out.snapshot = {"classification": level.value}
        out.source_ref = {}
        out.redacted = True
    return out


class EvidenceService:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def list_page(
        self, investigation_id: uuid.UUID, actor: Actor, *, levels: list[AssertionLevel] | None, limit: int, offset: int
    ) -> Page[EvidenceRead]:
        async with self.db.read_session() as s:
            rows, total = await EvidenceRepository(s).list_page(
                investigation_id, levels=levels, limit=limit, offset=offset
            )
            return Page(items=[redact(e, actor) for e in rows], total=total, limit=limit, offset=offset)

    async def findings(self, investigation_id: uuid.UUID) -> list[FindingRead]:
        async with self.db.read_session() as s:
            return [
                FindingRead(
                    id=f.id,
                    statement=f.statement,
                    category=f.category,
                    assertion_level=f.assertion_level,
                    is_supported=f.is_supported,
                    produced_by_node=f.produced_by_node,
                    evidence_refs=refs,
                )
                for f, refs in await EvidenceRepository(s).findings(investigation_id)
            ]
