"""Chunk stores (D-009, D-026, D-034).

Both stores apply the access filter *inside* the search, so a chunk above the caller's
clearance never comes back, whatever its score.
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Any, LiteralString, Protocol

from neo4j.exceptions import ClientError

from app.infrastructure.llm.embeddings import tokenize
from app.infrastructure.neo4j.client import Neo4jClient
from app.infrastructure.observability.logging import get_logger

from .models import ChunkRecord, RetrievalFilters

log = get_logger("rag.store")
Hit = tuple[ChunkRecord, float]


class ChunkStore(Protocol):
    async def replace_document(self, document_id: str, chunks: list[ChunkRecord]) -> None: ...
    async def delete_document(self, document_id: str) -> None: ...
    async def vector_search(self, embedding: list[float], k: int, filters: RetrievalFilters) -> list[Hit]: ...
    async def keyword_search(self, query: str, k: int, filters: RetrievalFilters) -> list[Hit]: ...


def _allowed(c: ChunkRecord, f: RetrievalFilters) -> bool:
    return c.classification.rank <= f.max_classification.rank and (f.doc_group is None or c.doc_group == f.doc_group)


class InMemoryChunkStore:
    """Test double: exact cosine search + BM25 keyword search."""

    def __init__(self) -> None:
        self.chunks: dict[str, ChunkRecord] = {}

    async def replace_document(self, document_id: str, chunks: list[ChunkRecord]) -> None:
        await self.delete_document(document_id)
        for c in chunks:
            self.chunks[c.chunk_id] = c

    async def delete_document(self, document_id: str) -> None:
        for cid in [k for k, c in self.chunks.items() if c.document_id == document_id]:
            del self.chunks[cid]

    async def vector_search(self, embedding: list[float], k: int, filters: RetrievalFilters) -> list[Hit]:
        hits = []
        for c in self.chunks.values():
            if c.embedding and _allowed(c, filters):
                dot = sum(a * b for a, b in zip(embedding, c.embedding, strict=True))
                na = math.sqrt(sum(a * a for a in embedding)) or 1.0
                nb = math.sqrt(sum(b * b for b in c.embedding)) or 1.0
                hits.append((c, (1 + dot / (na * nb)) / 2))  # Neo4j-style cosine score in [0, 1]
        return sorted(hits, key=lambda h: -h[1])[:k]

    async def keyword_search(self, query: str, k: int, filters: RetrievalFilters) -> list[Hit]:
        docs = [c for c in self.chunks.values() if _allowed(c, filters)]
        if not docs:
            return []
        toks = {c.chunk_id: tokenize(c.text) for c in docs}
        avg = sum(len(t) for t in toks.values()) / len(docs)
        df = Counter(t for ts in toks.values() for t in set(ts))
        q = set(tokenize(query))
        hits = []
        for c in docs:
            tf = Counter(toks[c.chunk_id])
            score = 0.0
            for t in q:
                if tf[t]:
                    idf = math.log(1 + (len(docs) - df[t] + 0.5) / (df[t] + 0.5))
                    score += idf * tf[t] * 2.2 / (tf[t] + 1.2 * (0.25 + 0.75 * len(toks[c.chunk_id]) / avg))
            if score > 0:
                hits.append((c, score))
        return sorted(hits, key=lambda h: -h[1])[:k]


_RETURN = """
RETURN c.chunk_id AS chunk_id, c.document_id AS document_id, c.title AS title, c.doc_type AS doc_type,
       c.classification AS classification, c.case_id AS case_id, c.page AS page, c.section AS section,
       c.position AS position, c.text AS text, c.mentions AS mentions, score
ORDER BY score DESC LIMIT $k"""

VECTOR_SEARCH: LiteralString = (
    """CYPHER 25
MATCH (c:Chunk)
  SEARCH c IN (VECTOR INDEX chunk_embedding FOR $embedding
               WHERE c.classification_rank <= $max_rank
               LIMIT $k) SCORE AS score
"""
    + _RETURN
)
VECTOR_SEARCH_GROUP: LiteralString = (
    """CYPHER 25
MATCH (c:Chunk)
  SEARCH c IN (VECTOR INDEX chunk_embedding FOR $embedding
               WHERE c.classification_rank <= $max_rank AND c.doc_group = $doc_group
               LIMIT $k) SCORE AS score
"""
    + _RETURN
)
# Fallback for servers without in-index filtering: oversample, then post-filter (D-034).
VECTOR_SEARCH_FALLBACK: LiteralString = (
    """
CALL db.index.vector.queryNodes('chunk_embedding', $k_over, $embedding) YIELD node AS c, score
WHERE c.classification_rank <= $max_rank AND ($doc_group IS NULL OR c.doc_group = $doc_group)
"""
    + _RETURN
)
KEYWORD_SEARCH: LiteralString = (
    """
CALL db.index.fulltext.queryNodes('chunk_text', $query, {limit: $k_over}) YIELD node AS c, score
WHERE c.classification_rank <= $max_rank AND ($doc_group IS NULL OR c.doc_group = $doc_group)
"""
    + _RETURN
)

DELETE_DOCUMENT: LiteralString = """
MATCH (d:Document {document_id: $document_id})
OPTIONAL MATCH (d)-[:HAS_CHUNK]->(c:Chunk)
DETACH DELETE c, d
"""
UPSERT_DOCUMENT: LiteralString = """
MERGE (d:Document {document_id: $document_id})
SET d.title = $title, d.doc_type = $doc_type, d.classification = $classification, d.case_id = $case_id
WITH d
UNWIND $chunks AS r
CREATE (c:Chunk {chunk_id: r.chunk_id, document_id: $document_id, title: $title, doc_type: $doc_type,
                 doc_group: $doc_group, classification: $classification, classification_rank: $rank,
                 case_id: $case_id, page: r.page, section: r.section, position: r.position, text: r.text,
                 mentions: r.mentions, embedding: r.embedding})
CREATE (d)-[:HAS_CHUNK]->(c)
WITH c, r
OPTIONAL MATCH (f:FraudCase {case_id: $case_id})
FOREACH (_ IN CASE WHEN f IS NULL THEN [] ELSE [1] END | MERGE (c)-[:MENTIONS]->(f))
WITH c, r
UNWIND r.mentions AS acct
MATCH (a:Account {account_id: acct})
MERGE (c)-[:MENTIONS]->(a)
"""

_LUCENE_SPECIAL = set('+-&|!(){}[]^"~*?:\\/')


def lucene_escape(text: str) -> str:
    """User text must never be interpreted as Lucene query syntax."""
    return "".join(f"\\{ch}" if ch in _LUCENE_SPECIAL else ch for ch in text)


class Neo4jChunkStore:
    def __init__(self, client: Neo4jClient) -> None:
        self.client = client
        self._filtered_search = True  # flips to False once if the server lacks SEARCH filters

    async def replace_document(self, document_id: str, chunks: list[ChunkRecord]) -> None:
        await self.client.write(DELETE_DOCUMENT, {"document_id": document_id})
        if not chunks:
            return
        head = chunks[0]
        await self.client.write(
            UPSERT_DOCUMENT,
            {
                "document_id": document_id,
                "title": head.title,
                "doc_type": head.doc_type.value,
                "doc_group": head.doc_group,
                "classification": head.classification.value,
                "rank": head.classification.rank,
                "case_id": head.case_id,
                "chunks": [
                    {
                        "chunk_id": c.chunk_id,
                        "page": c.page,
                        "section": c.section,
                        "position": c.position,
                        "text": c.text,
                        "mentions": c.mentions,
                        "embedding": c.embedding,
                    }
                    for c in chunks
                ],
            },
        )

    async def delete_document(self, document_id: str) -> None:
        await self.client.write(DELETE_DOCUMENT, {"document_id": document_id})

    @staticmethod
    def _hits(rows: list[dict[str, Any]]) -> list[Hit]:
        return [
            (ChunkRecord.model_validate({k: v for k, v in r.items() if k != "score"}), float(r["score"])) for r in rows
        ]

    async def vector_search(self, embedding: list[float], k: int, filters: RetrievalFilters) -> list[Hit]:
        params = {
            "embedding": embedding,
            "k": k,
            "k_over": k * 5,
            "max_rank": filters.max_classification.rank,
            "doc_group": filters.doc_group,
        }
        if self._filtered_search:
            try:
                q = VECTOR_SEARCH_GROUP if filters.doc_group else VECTOR_SEARCH
                return self._hits(await self.client.read(q, params))
            except ClientError as exc:
                log.warning("rag.vector_search_fallback", code=exc.code)
                self._filtered_search = False
        return self._hits(await self.client.read(VECTOR_SEARCH_FALLBACK, params))

    async def keyword_search(self, query: str, k: int, filters: RetrievalFilters) -> list[Hit]:
        params = {
            "query": lucene_escape(query),
            "k": k,
            "k_over": k * 5,
            "max_rank": filters.max_classification.rank,
            "doc_group": filters.doc_group,
        }
        return self._hits(await self.client.read(KEYWORD_SEARCH, params))
