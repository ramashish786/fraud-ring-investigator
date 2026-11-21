from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text

from app.core.enums import Classification, DocumentStatus, DocumentType, Role
from app.core.errors import PermissionDeniedError, ValidationFailedError
from app.domains.documents.schemas import DocumentMeta
from app.domains.documents.service import DocumentService, IngestionService
from app.infrastructure.database.engine import Database
from app.infrastructure.jobs.repository import JobRepository
from app.infrastructure.llm.embeddings import HashingEmbedder
from app.infrastructure.storage import LocalFileStorage
from app.rag.models import RetrievalFilters
from app.rag.retrieval.hybrid import HybridSearch
from app.rag.store import InMemoryChunkStore

pytestmark = pytest.mark.integration
META = DocumentMeta(title="Mule policy", doc_type=DocumentType.POLICY, classification=Classification.INTERNAL)
BODY = b"# Mule policy\n\n## Red flags\nRapid pass-through and accounts A000123 receiving funds.\n"


async def test_upload_ingest_search_round_trip(db: Database, make_actor: Any, tmp_path: Path) -> None:
    analyst = await make_actor(Role.ANALYST, clearance=Classification.CONFIDENTIAL)
    storage = LocalFileStorage(tmp_path)
    docs = DocumentService(db, storage, 1024 * 1024)
    doc, created = await docs.upload("mule.md", BODY, META, analyst)
    again, created_again = await docs.upload("copy.md", BODY, META, analyst)
    assert created and not created_again and again.id == doc.id  # content-addressed dedupe

    async with db.unit_of_work() as s:
        job = await JobRepository(s).claim("w", 60)
    assert job and job.document_id == doc.id

    store, emb = InMemoryChunkStore(), HashingEmbedder(64)
    n = await IngestionService(db, storage, store, emb).process(doc.id)
    assert n >= 1
    page = await docs.list_page(analyst, limit=10, offset=0)
    assert page.items[0].status == DocumentStatus.INDEXED and page.items[0].chunk_count == n
    hits = await HybridSearch(store, emb).search("pass-through", RetrievalFilters())
    assert hits[0].citation.document_id == str(doc.id) and hits[0].citation.section == "Red flags"
    assert "A000123" in store.chunks[hits[0].citation.chunk_id].mentions


async def test_clearance_rules(db: Database, make_actor: Any, tmp_path: Path) -> None:
    low = await make_actor(Role.ANALYST, clearance=Classification.INTERNAL)
    docs = DocumentService(db, LocalFileStorage(tmp_path), 1024 * 1024)
    with pytest.raises(PermissionDeniedError):
        await docs.upload("x.md", BODY, META.model_copy(update={"classification": Classification.RESTRICTED}), low)
    reviewer = await make_actor(Role.REVIEWER)
    with pytest.raises(PermissionDeniedError):
        await docs.upload("x.md", BODY, META, reviewer)
    with pytest.raises(ValidationFailedError):
        await docs.upload("x.exe", b"MZ", META, low)


async def test_failed_ingestion_is_recorded(db: Database, make_actor: Any, tmp_path: Path) -> None:
    analyst = await make_actor(Role.ANALYST)
    storage = LocalFileStorage(tmp_path)
    doc, _ = await DocumentService(db, storage, 1024 * 1024).upload("bad.pdf", b"%PDF-1.4 broken", META, analyst)
    with pytest.raises(Exception):  # noqa: B017 - any parser error must propagate so the job retries
        await IngestionService(db, storage, InMemoryChunkStore(), HashingEmbedder(64)).process(doc.id)
    async with db.read_session() as s:
        status, error = (
            await s.execute(text("SELECT status, error FROM documents WHERE id = :i"), {"i": doc.id})
        ).one()
    assert status == "failed" and error
