"""Document upload (API side) and ingestion (worker side).

Upload: validate -> store bytes (content-addressed) -> metadata row + ingest job in one
transaction -> 202. Same bytes again -> the existing document (idempotent).
Ingestion: status machine uploaded -> processing -> indexed | failed. Re-running replaces
the document's chunks, so retries are safe.
"""

from __future__ import annotations

import hashlib
import uuid
from pathlib import PurePath

from sqlalchemy import func, select

from app.core.enums import Classification, DocumentStatus, JobKind, Role
from app.core.errors import NotFoundError, PermissionDeniedError
from app.core.pagination import Page
from app.domains.audit.service import record
from app.domains.identity.actor import Actor
from app.infrastructure.database.engine import Database
from app.infrastructure.jobs.repository import JobRepository
from app.infrastructure.llm.embeddings import Embedder
from app.infrastructure.observability.logging import get_logger
from app.infrastructure.storage import LocalFileStorage
from app.rag.ingestion.chunker import ACCOUNT_ID, chunk_pages
from app.rag.ingestion.extract import extract_pages
from app.rag.ingestion.validation import sniff_mime
from app.rag.models import ChunkRecord
from app.rag.store import ChunkStore

from .models import Document
from .schemas import DocumentMeta, DocumentRead

log = get_logger("documents")
EMBED_BATCH = 64


class DocumentService:
    def __init__(self, db: Database, storage: LocalFileStorage, max_upload_bytes: int) -> None:
        self.db = db
        self.storage = storage
        self.max_upload_bytes = max_upload_bytes

    async def upload(self, filename: str, data: bytes, meta: DocumentMeta, actor: Actor) -> tuple[DocumentRead, bool]:
        if not actor.has_any(Role.ANALYST, Role.ADMIN):
            raise PermissionDeniedError("Only analysts and admins can upload documents")
        if meta.classification.rank > actor.clearance.rank:
            raise PermissionDeniedError("Cannot upload a document above your own clearance")
        mime = sniff_mime(filename, data, self.max_upload_bytes)
        sha = hashlib.sha256(data).hexdigest()
        async with self.db.unit_of_work() as s:
            existing = await s.scalar(select(Document).where(Document.sha256 == sha, Document.deleted_at.is_(None)))
            if existing:
                return DocumentRead.model_validate(existing), False
            path = await self.storage.put(sha, PurePath(filename).suffix.lower(), data)
            doc = Document(
                **meta.model_dump(),
                mime_type=mime,
                size_bytes=len(data),
                sha256=sha,
                storage_path=path,
                uploaded_by=actor.user_id,
            )
            s.add(doc)
            await s.flush()
            await JobRepository(s).enqueue(JobKind.INGEST_DOCUMENT, document_id=doc.id, max_attempts=3)
            await record(
                s,
                actor,
                "document.uploaded",
                "document",
                str(doc.id),
                {"classification": meta.classification.value, "doc_type": meta.doc_type.value},
            )
            await s.refresh(doc)
            return DocumentRead.model_validate(doc), True

    async def list_page(self, actor: Actor, *, limit: int, offset: int) -> Page[DocumentRead]:
        visible = [c for c in Classification if c.rank <= actor.clearance.rank]
        async with self.db.read_session() as s:
            base = select(Document).where(Document.deleted_at.is_(None), Document.classification.in_(visible))
            total = await s.scalar(select(func.count()).select_from(base.subquery())) or 0
            rows = (await s.scalars(base.order_by(Document.created_at.desc()).limit(limit).offset(offset))).all()
            return Page(items=[DocumentRead.model_validate(d) for d in rows], total=total, limit=limit, offset=offset)


def build_chunks(document_id: str, meta: DocumentRead | DocumentMeta, mime_type: str, data: bytes) -> list[ChunkRecord]:
    """Pure: bytes -> chunk records (no I/O). Shared by the worker pipeline and offline evaluation."""
    pieces = chunk_pages(extract_pages(data, mime_type))
    return [
        ChunkRecord(
            chunk_id=f"{document_id}:{i}",
            document_id=document_id,
            title=meta.title,
            doc_type=meta.doc_type,
            classification=meta.classification,
            case_id=meta.case_id,
            page=p.page,
            section=p.section,
            position=i,
            text=p.text,
            mentions=sorted(set(ACCOUNT_ID.findall(p.text))),
        )
        for i, p in enumerate(pieces)
    ]


async def embed_chunks(embedder: Embedder, records: list[ChunkRecord]) -> None:
    for i in range(0, len(records), EMBED_BATCH):
        batch = records[i : i + EMBED_BATCH]
        vectors = await embedder.embed_documents([f"{r.title}\n{r.section or ''}\n{r.text}" for r in batch])
        for r, v in zip(batch, vectors, strict=True):
            r.embedding = v


class IngestionService:
    def __init__(self, db: Database, storage: LocalFileStorage, store: ChunkStore, embedder: Embedder) -> None:
        self.db = db
        self.storage = storage
        self.store = store
        self.embedder = embedder

    async def process(self, document_id: uuid.UUID) -> int:
        async with self.db.unit_of_work() as s:
            doc = await s.get(Document, document_id, with_for_update=True)
            if not doc or doc.deleted_at:
                raise NotFoundError(f"Document {document_id} not found")
            doc.status, doc.error = DocumentStatus.PROCESSING, None
            meta = DocumentRead.model_validate(doc)
            path = doc.storage_path
        try:
            records = build_chunks(str(document_id), meta, meta.mime_type, await self.storage.get(path))
            await embed_chunks(self.embedder, records)
            await self.store.replace_document(str(document_id), records)
        except Exception as exc:
            async with self.db.unit_of_work() as s:
                doc = await s.get(Document, document_id)
                if doc:
                    doc.status, doc.error = DocumentStatus.FAILED, f"{type(exc).__name__}: {exc}"[:500]
            log.warning("documents.ingest_failed", document_id=str(document_id), error_type=type(exc).__name__)
            raise
        async with self.db.unit_of_work() as s:
            doc = await s.get(Document, document_id)
            assert doc is not None
            doc.status, doc.chunk_count = DocumentStatus.INDEXED, len(records)
        log.info("documents.indexed", document_id=str(document_id), chunks=len(records))
        return len(records)
