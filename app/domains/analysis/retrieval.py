"""Historical case and policy retrieval as evidence (deterministic part of nodes 5 and 6).

Each retrieved passage becomes VERIFIED_FACT evidence ("document X says ..."), carrying
the document's classification, so the LLM can only cite passages that were actually
retrieved under the caller's clearance (D-012, D-033).
"""

from __future__ import annotations

from app.core.enums import AssertionLevel, Classification, EvidenceSource
from app.domains.evidence.schemas import EvidenceItem
from app.rag.models import DocGroup, RetrievalFilters, RetrievedChunk
from app.rag.retrieval.hybrid import HybridSearch

from .signals import AnalysisResult

MAX_PASSAGE_CHARS = 1200


def passage_evidence(h: RetrievedChunk) -> EvidenceItem:
    c = h.citation
    where = f"{c.title}" + (f", section '{c.section}'" if c.section else "") + f", page {c.page}"
    return EvidenceItem(
        kind="document_passage",
        assertion_level=AssertionLevel.VERIFIED_FACT,
        source=EvidenceSource.DOCUMENT,
        classification=c.classification,
        source_ref={"document_id": c.document_id, "chunk_id": c.chunk_id},
        snapshot={
            **c.model_dump(mode="json"),
            "text": h.text[:MAX_PASSAGE_CHARS],
            "score": h.score,
            "retrievers": h.retrievers,
        },
        summary=f"{where}: {h.text[:160].replace(chr(10), ' ')}{'…' if len(h.text) > 160 else ''}",
    )


async def retrieve(
    search: HybridSearch, query: str, group: DocGroup, clearance: Classification, k: int = 5
) -> AnalysisResult:
    res = AnalysisResult()
    hits = await search.search(query, RetrievalFilters(max_classification=clearance, doc_group=group), k=k)
    for h in hits:
        res.add(passage_evidence(h))
    if not hits:
        res.notes.append(f"No {'historical cases' if group == 'case' else 'policy documents'} matched the query")
    return res
