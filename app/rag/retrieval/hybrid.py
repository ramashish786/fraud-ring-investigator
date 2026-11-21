"""Hybrid retrieval: vector + keyword, fused with Reciprocal Rank Fusion (D-009).

RRF uses ranks, not raw scores, so cosine similarities and BM25/Lucene scores (different
scales) combine without tuning: score(d) = sum over retrievers of 1 / (k + rank).
"""

from __future__ import annotations

import time
from typing import Literal

from langchain_core.callbacks import AsyncCallbackManagerForRetrieverRun, CallbackManagerForRetrieverRun
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from pydantic import ConfigDict

from app.infrastructure.llm.embeddings import Embedder
from app.infrastructure.observability.logging import get_logger
from app.infrastructure.observability.metrics import RETRIEVAL_LATENCY

from ..models import ChunkRecord, Citation, RetrievalFilters, RetrievedChunk
from ..store import ChunkStore, Hit

log = get_logger("rag.retrieval")
Mode = Literal["vector", "keyword", "hybrid"]
RRF_K = 60


def _citation(c: ChunkRecord) -> Citation:
    return Citation(
        document_id=c.document_id,
        chunk_id=c.chunk_id,
        title=c.title,
        doc_type=c.doc_type,
        classification=c.classification,
        case_id=c.case_id,
        page=c.page,
        section=c.section,
    )


def rrf(result_lists: dict[str, list[Hit]], k: int) -> list[RetrievedChunk]:
    scores: dict[str, float] = {}
    found_by: dict[str, list[str]] = {}
    chunks: dict[str, ChunkRecord] = {}
    for name, hits in result_lists.items():
        for rank, (c, _) in enumerate(hits, start=1):
            scores[c.chunk_id] = scores.get(c.chunk_id, 0.0) + 1.0 / (RRF_K + rank)
            found_by.setdefault(c.chunk_id, []).append(name)
            chunks[c.chunk_id] = c
    ranked = sorted(scores, key=lambda cid: -scores[cid])[:k]
    return [
        RetrievedChunk(
            citation=_citation(chunks[cid]),
            text=chunks[cid].text,
            score=round(scores[cid], 5),
            retrievers=found_by[cid],
        )
        for cid in ranked
    ]


class HybridSearch:
    def __init__(self, store: ChunkStore, embedder: Embedder) -> None:
        self.store = store
        self.embedder = embedder

    async def search(
        self, query: str, filters: RetrievalFilters, *, k: int = 5, mode: Mode = "hybrid"
    ) -> list[RetrievedChunk]:
        start = time.perf_counter()
        lists: dict[str, list[Hit]] = {}
        if mode in ("vector", "hybrid"):
            lists["vector"] = await self.store.vector_search(await self.embedder.embed_query(query), k * 2, filters)
        if mode in ("keyword", "hybrid"):
            lists["keyword"] = await self.store.keyword_search(query, k * 2, filters)
        out = rrf(lists, k)
        RETRIEVAL_LATENCY.labels(mode).observe(time.perf_counter() - start)
        log.info(
            "rag.search",
            mode=mode,
            k=k,
            doc_group=filters.doc_group,
            results=len(out),
            latency_ms=round((time.perf_counter() - start) * 1000, 1),
        )
        return out


class HybridRetriever(BaseRetriever):
    """LangChain retriever adapter (spec §7): Documents carry the citation in metadata."""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    search: HybridSearch
    filters: RetrievalFilters
    k: int = 5
    mode: Mode = "hybrid"

    def _get_relevant_documents(self, query: str, *, run_manager: CallbackManagerForRetrieverRun) -> list[Document]:
        raise NotImplementedError("HybridRetriever is async-only; use ainvoke()")

    async def _aget_relevant_documents(
        self, query: str, *, run_manager: AsyncCallbackManagerForRetrieverRun
    ) -> list[Document]:
        hits = await self.search.search(query, self.filters, k=self.k, mode=self.mode)
        return [
            Document(
                page_content=h.text,
                metadata={"citation": h.citation.model_dump(mode="json"), "score": h.score, "retrievers": h.retrievers},
            )
            for h in hits
        ]
