from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, File, Form, Query, Response, UploadFile, status

from app.api.deps import ContainerDep, Reader, Uploader
from app.core.enums import Classification, DocumentType
from app.core.errors import ValidationFailedError
from app.core.pagination import Page
from app.domains.documents.schemas import DocumentMeta, DocumentRead
from app.domains.documents.service import DocumentService

router = APIRouter(prefix="/documents", tags=["documents"])


@router.post(
    "",
    response_model=DocumentRead,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Upload a document; ingestion (chunk, embed, index) runs in the worker",
)
async def upload(
    c: ContainerDep,
    actor: Uploader,
    response: Response,
    file: Annotated[UploadFile, File()],
    title: Annotated[str, Form(max_length=300)],
    doc_type: Annotated[DocumentType, Form()],
    classification: Annotated[Classification, Form()],
    source: Annotated[str, Form(max_length=200)] = "upload",
    case_id: Annotated[str | None, Form(max_length=64)] = None,
) -> DocumentRead:
    limit = c.settings.max_upload_bytes
    data = await file.read(limit + 1)  # never read an unbounded body into memory
    if len(data) > limit:
        raise ValidationFailedError(f"File exceeds the {limit // (1024 * 1024)} MB limit")
    assert c.storage is not None
    meta = DocumentMeta(
        title=title, doc_type=doc_type, classification=classification, source=source, case_id=case_id or None
    )
    doc, created = await DocumentService(c.db, c.storage, limit).upload(file.filename or "upload", data, meta, actor)
    if not created:
        response.status_code = status.HTTP_200_OK
    return doc


@router.get("", response_model=Page[DocumentRead], summary="Documents visible at your clearance")
async def list_documents(
    c: ContainerDep,
    actor: Reader,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[DocumentRead]:
    assert c.storage is not None
    return await DocumentService(c.db, c.storage, c.settings.max_upload_bytes).list_page(
        actor, limit=limit, offset=offset
    )
