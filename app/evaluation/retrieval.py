"""Retrieval evaluation: Recall@K and MRR for vector, keyword and hybrid modes (spec §7, §14).

    python -m app.evaluation.retrieval --data data [--provider fake|openai]

Uses the labelled queries from simulation/documents.py. Runs against an in-memory chunk
store built with the same chunker and embedder as production ingestion.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from app.core.enums import Classification, DocumentType
from app.domains.documents.schemas import DocumentMeta
from app.domains.documents.service import build_chunks, embed_chunks
from app.infrastructure.llm.embeddings import Embedder, HashingEmbedder
from app.rag.models import RetrievalFilters
from app.rag.retrieval.hybrid import HybridSearch, Mode
from app.rag.store import ChunkStore, InMemoryChunkStore


async def ingest_corpus(data: Path, store: ChunkStore, embedder: Embedder) -> dict[str, str]:
    """Ingest data/documents/manifest.json; returns document_id -> manifest key."""
    manifest = json.loads((data / "documents/manifest.json").read_text())
    ids = {}
    for m in manifest:
        doc_id = f"doc-{m['key']}"
        meta = DocumentMeta(
            title=m["title"],
            doc_type=DocumentType(m["doc_type"]),
            classification=Classification(m["classification"]),
            source=m["source"],
            case_id=m.get("case_id"),
        )
        records = build_chunks(doc_id, meta, "text/markdown", (data / m["path"]).read_bytes())
        await embed_chunks(embedder, records)
        await store.replace_document(doc_id, records)
        ids[doc_id] = m["key"]
    return ids


async def evaluate(data: Path, embedder: Embedder, ks: tuple[int, ...] = (1, 3, 5)) -> dict[str, Any]:
    store = InMemoryChunkStore()
    ids = await ingest_corpus(data, store, embedder)
    search = HybridSearch(store, embedder)
    queries = json.loads((data / "documents/retrieval_eval.json").read_text())
    results: dict[str, Any] = {}
    modes: tuple[Mode, ...] = ("vector", "keyword", "hybrid")
    for mode in modes:
        recall = {k: 0.0 for k in ks}
        mrr = 0.0
        for q in queries:
            hits = await search.search(
                q["query"],
                RetrievalFilters(max_classification=Classification.RESTRICTED, doc_group=q["group"]),
                k=max(ks) * 3,
                mode=mode,
            )
            docs = list(dict.fromkeys(ids[h.citation.document_id] for h in hits))  # rank documents, not chunks
            relevant = set(q["relevant"])
            for k in ks:
                recall[k] += len(relevant & set(docs[:k])) / len(relevant)
            first = next((i for i, d in enumerate(docs, 1) if d in relevant), None)
            mrr += 1 / first if first else 0.0
        n = len(queries)
        results[mode] = {**{f"recall@{k}": round(v / n, 3) for k, v in recall.items()}, "mrr": round(mrr / n, 3)}
    results["queries"] = len(queries)
    results["embedder"] = embedder.name
    return results


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--data", default="data")
    p.add_argument("--provider", choices=["fake", "openai"], default="fake")
    a = p.parse_args()
    if a.provider == "openai":
        from app.core.settings import get_settings
        from app.infrastructure.llm.embeddings import OpenAIEmbedder

        embedder: Embedder = OpenAIEmbedder(get_settings())
    else:
        embedder = HashingEmbedder(1536)
    print(json.dumps(asyncio.run(evaluate(Path(a.data), embedder)), indent=2))


if __name__ == "__main__":
    main()
