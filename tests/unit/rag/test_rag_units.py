from __future__ import annotations

import pytest

from app.core.enums import Classification, DocumentType
from app.core.errors import ValidationFailedError
from app.infrastructure.llm.embeddings import HashingEmbedder
from app.rag.ingestion.chunker import chunk_pages
from app.rag.ingestion.validation import sniff_mime
from app.rag.models import ChunkRecord, RetrievalFilters
from app.rag.retrieval.hybrid import HybridRetriever, HybridSearch, rrf
from app.rag.store import InMemoryChunkStore, lucene_escape

DOC = """# Policy
Intro line.

## Red flags
- Rapid pass-through of funds.
- New beneficiaries.

## Actions
""" + ("Escalate the case. " * 120)


def test_chunker_respects_sections_and_size() -> None:
    chunks = chunk_pages([(1, DOC)], max_chars=400, overlap=50)
    assert {c.section for c in chunks} == {"Policy", "Red flags", "Actions"}
    assert all(len(c.text) <= 450 for c in chunks)
    assert sum(c.section == "Actions" for c in chunks) >= 3  # long section split


@pytest.mark.parametrize(
    ("name", "data", "ok"),
    [
        ("a.md", b"# hi", True),
        ("a.pdf", b"%PDF-1.7 ...", True),
        ("a.pdf", b"not a pdf", False),
        ("a.exe", b"MZ", False),
        ("a.txt", b"\x00\x01binary", False),
        ("a.txt", "é".encode("latin-1"), False),
        ("a.txt", b"", False),
    ],
)
def test_upload_validation(name: str, data: bytes, ok: bool) -> None:
    if ok:
        assert sniff_mime(name, data, 1024)
    else:
        with pytest.raises(ValidationFailedError):
            sniff_mime(name, data, 1024)


def test_size_limit() -> None:
    with pytest.raises(ValidationFailedError, match="limit"):
        sniff_mime("a.md", b"x" * 2048, 1024)


def test_lucene_escape_neutralises_syntax() -> None:
    assert lucene_escape('a AND b:(c*) "x"') == 'a AND b\\:\\(c\\*\\) \\"x\\"'


async def test_hashing_embedder_is_deterministic_and_normalised() -> None:
    e = HashingEmbedder(128)
    a, b = await e.embed_query("mule pass-through"), await e.embed_query("mule pass-through")
    assert a == b and abs(sum(x * x for x in a) - 1) < 1e-9


def _chunk(cid: str, text: str, cls: Classification, dt: DocumentType = DocumentType.POLICY) -> ChunkRecord:
    return ChunkRecord(
        chunk_id=cid, document_id=cid.split(":")[0], title=cid, doc_type=dt, classification=cls, position=0, text=text
    )


async def test_access_filter_applied_inside_search() -> None:
    store, emb = InMemoryChunkStore(), HashingEmbedder(64)
    chunks = [
        _chunk("pub:0", "mule accounts forward funds", Classification.PUBLIC),
        _chunk("sec:0", "mule accounts forward funds secret referral", Classification.RESTRICTED),
    ]
    for c in chunks:
        c.embedding = await emb.embed_query(c.text)
        await store.replace_document(c.document_id, [c])
    search = HybridSearch(store, emb)
    low = await search.search("mule referral", RetrievalFilters(max_classification=Classification.INTERNAL), k=5)
    high = await search.search("mule referral", RetrievalFilters(max_classification=Classification.RESTRICTED), k=5)
    assert [h.citation.chunk_id for h in low] == ["pub:0"]
    assert {h.citation.chunk_id for h in high} == {"pub:0", "sec:0"}


def test_rrf_rewards_agreement() -> None:
    a, b, c = (_chunk(x, x, Classification.PUBLIC) for x in ("a:0", "b:0", "c:0"))
    fused = rrf({"vector": [(a, 0.9), (b, 0.8)], "keyword": [(b, 12.0), (c, 3.0)]}, k=3)
    assert fused[0].citation.chunk_id == "b:0" and fused[0].retrievers == ["vector", "keyword"]


async def test_langchain_retriever_adapter_keeps_citations() -> None:
    store, emb = InMemoryChunkStore(), HashingEmbedder(64)
    c = _chunk("doc:0", "structuring below the reporting threshold", Classification.PUBLIC)
    c.embedding = await emb.embed_query(c.text)
    await store.replace_document("doc", [c])
    retriever = HybridRetriever(search=HybridSearch(store, emb), filters=RetrievalFilters(), k=2)
    docs = await retriever.ainvoke("reporting threshold")
    assert docs and docs[0].metadata["citation"]["chunk_id"] == "doc:0"


async def test_retrieval_quality_floor(tmp_path: object) -> None:
    """Regression guard on the labelled evaluation set (lexical embedder)."""
    from pathlib import Path

    from app.evaluation.retrieval import evaluate
    from simulation.config import SimConfig
    from simulation.documents import generate
    from simulation.simulate import run, write

    root = Path(str(tmp_path))
    write(run(SimConfig(seed=5, n_customers=300, days=30)), root)
    generate(root)
    res = await evaluate(root, HashingEmbedder(512))
    assert res["hybrid"]["recall@5"] >= 0.8 and res["hybrid"]["mrr"] >= 0.7
