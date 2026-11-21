"""ChunkStore contract: memory always; Neo4j when TEST_NEO4J_URI is set."""

from __future__ import annotations

import asyncio
import os
from typing import Any

import pytest

from app.core.enums import Classification, DocumentType
from app.infrastructure.llm.embeddings import HashingEmbedder
from app.rag.models import ChunkRecord, RetrievalFilters
from app.rag.store import InMemoryChunkStore

EMB = HashingEmbedder(64)


@pytest.fixture(scope="module", params=["memory", "neo4j"])
def store(request: pytest.FixtureRequest) -> Any:
    if request.param == "memory":
        return InMemoryChunkStore()
    uri = os.environ.get("TEST_NEO4J_URI")
    if not uri:
        if os.environ.get("REQUIRE_NEO4J") == "1":
            pytest.fail("REQUIRE_NEO4J=1 but TEST_NEO4J_URI is not set")
        pytest.skip("neo4j backend: set TEST_NEO4J_URI")
    from app.core.settings import Settings
    from app.graph.schema.bootstrap import apply_schema
    from app.infrastructure.neo4j.client import Neo4jClient
    from app.rag.store import Neo4jChunkStore

    s = Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://unused/x",
        jwt_secret="x" * 40,  # type: ignore[call-arg]
        neo4j_uri=uri,
        neo4j_password=os.environ.get("TEST_NEO4J_PASSWORD", "neo4j_dev_pw"),
        embedding_dimensions=64,
    )
    asyncio.run(apply_schema(Neo4jClient.from_settings(s), 64))
    return Neo4jChunkStore(Neo4jClient.from_settings(s))


async def _doc(store: Any, doc_id: str, text: str, cls: Classification, dt: DocumentType) -> None:
    c = ChunkRecord(
        chunk_id=f"{doc_id}:0",
        document_id=doc_id,
        title=doc_id,
        doc_type=dt,
        classification=cls,
        position=0,
        text=text,
        embedding=await EMB.embed_query(text),
    )
    await store.replace_document(doc_id, [c])


async def test_search_filters_and_replace(store: Any) -> None:
    await _doc(
        store, "ct-policy", "mule accounts forward stolen funds quickly", Classification.INTERNAL, DocumentType.POLICY
    )
    await _doc(
        store,
        "ct-case",
        "mule accounts forward stolen funds within hours",
        Classification.CONFIDENTIAL,
        DocumentType.CASE_REPORT,
    )
    await _doc(
        store, "ct-secret", "mule referral to law enforcement", Classification.RESTRICTED, DocumentType.PROCEDURE
    )
    q = await EMB.embed_query("mule accounts forward funds")
    internal = await store.vector_search(q, 10, RetrievalFilters(max_classification=Classification.INTERNAL))
    assert {c.document_id for c, _ in internal if c.document_id.startswith("ct-")} == {"ct-policy"}
    cases = await store.vector_search(
        q, 10, RetrievalFilters(max_classification=Classification.RESTRICTED, doc_group="case")
    )
    assert {c.document_id for c, _ in cases if c.document_id.startswith("ct-")} == {"ct-case"}
    kw = await store.keyword_search(
        "law enforcement (referral)", 10, RetrievalFilters(max_classification=Classification.RESTRICTED)
    )
    assert any(c.document_id == "ct-secret" for c, _ in kw)
    await _doc(
        store, "ct-policy", "completely different text about thresholds", Classification.INTERNAL, DocumentType.POLICY
    )  # replace, not append
    again = await store.keyword_search("stolen", 10, RetrievalFilters(max_classification=Classification.INTERNAL))
    assert not any(c.document_id == "ct-policy" for c, _ in again)
    for d in ("ct-policy", "ct-case", "ct-secret"):
        await store.delete_document(d)
