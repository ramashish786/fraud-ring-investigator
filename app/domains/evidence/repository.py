from __future__ import annotations

import json
import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import AssertionLevel

from .models import Evidence, Finding, FindingEvidence
from .schemas import EvidenceItem, FindingIn


def _jsonable(value: dict[str, Any]) -> dict[str, Any]:
    """Snapshots may hold datetimes/Decimals; store them exactly as they were hashed (ISO strings)."""
    out: dict[str, Any] = json.loads(json.dumps(value, default=str))
    return out


class EvidenceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    async def add_many(self, investigation_id: uuid.UUID, items: Sequence[EvidenceItem], node: str) -> list[str]:
        """Idempotent: re-running a node inserts nothing new (unique content hash per investigation)."""
        if not items:
            return []
        rows = [
            {
                "investigation_id": investigation_id,
                "ref": it.ref,
                "kind": it.kind,
                "assertion_level": it.assertion_level.value,
                "source": it.source.value,
                "source_ref": _jsonable(it.source_ref),
                "snapshot": _jsonable({**it.snapshot, "classification": it.classification.value}),
                "summary": it.summary,
                "content_hash": it.content_hash,
                "produced_by_node": node,
            }
            for it in items
        ]
        await self.s.execute(
            insert(Evidence).values(rows).on_conflict_do_nothing(index_elements=["investigation_id", "content_hash"])
        )
        return list(dict.fromkeys(it.ref for it in items))

    async def list_page(
        self,
        investigation_id: uuid.UUID,
        *,
        levels: list[AssertionLevel] | None = None,
        refs: list[str] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[Evidence], int]:
        base = select(Evidence).where(Evidence.investigation_id == investigation_id)
        if levels:
            base = base.where(Evidence.assertion_level.in_(levels))
        if refs is not None:
            base = base.where(Evidence.ref.in_(refs))
        total = await self.s.scalar(select(func.count()).select_from(base.subquery())) or 0
        rows = (
            await self.s.scalars(base.order_by(Evidence.created_at, Evidence.ref).limit(limit).offset(offset))
        ).all()
        return list(rows), total

    async def existing_refs(self, investigation_id: uuid.UUID, refs: list[str]) -> set[str]:
        if not refs:
            return set()
        rows = await self.s.scalars(
            select(Evidence.ref).where(Evidence.investigation_id == investigation_id, Evidence.ref.in_(refs))
        )
        return set(rows.all())

    async def replace_findings(self, investigation_id: uuid.UUID, node: str, findings: list[FindingIn]) -> int:
        """Replace semantics per (investigation, node): a re-executed node doesn't duplicate findings."""
        await self.s.execute(
            delete(Finding).where(Finding.investigation_id == investigation_id, Finding.produced_by_node == node)
        )
        ids = {
            e.ref: e.id
            for e in (
                await self.s.scalars(
                    select(Evidence).where(
                        Evidence.investigation_id == investigation_id,
                        Evidence.ref.in_({r for f in findings for r in f.evidence_refs}),
                    )
                )
            ).all()
        }
        for pos, f in enumerate(findings):
            row = Finding(
                position=pos,
                investigation_id=investigation_id,
                statement=f.statement,
                category=f.category,
                assertion_level=f.assertion_level,
                is_supported=f.is_supported and all(r in ids for r in f.evidence_refs) and bool(f.evidence_refs),
                produced_by_node=node,
            )
            self.s.add(row)
            await self.s.flush()
            for r in dict.fromkeys(f.evidence_refs):
                if r in ids:
                    self.s.add(FindingEvidence(finding_id=row.id, evidence_id=ids[r]))
        await self.s.flush()
        return len(findings)

    async def findings(self, investigation_id: uuid.UUID) -> list[tuple[Finding, list[str]]]:
        rows = (
            await self.s.scalars(
                select(Finding)
                .where(Finding.investigation_id == investigation_id)
                .order_by(Finding.created_at, Finding.produced_by_node, Finding.position)
            )
        ).all()
        links = (
            await self.s.execute(
                select(FindingEvidence.finding_id, Evidence.ref)
                .join(Evidence, Evidence.id == FindingEvidence.evidence_id)
                .where(Evidence.investigation_id == investigation_id)
            )
        ).all()
        refs: dict[uuid.UUID, list[str]] = {}
        for fid, ref in links:
            refs.setdefault(fid, []).append(ref)
        return [(f, sorted(refs.get(f.id, []))) for f in rows]
