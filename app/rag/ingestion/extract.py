"""Text extraction. Returns (page_number, text) pairs so citations can point at pages."""

from __future__ import annotations

import io

from app.core.errors import ValidationFailedError

TEXT_TYPES = {"text/markdown", "text/plain"}
PDF_TYPE = "application/pdf"


def extract_pages(data: bytes, mime_type: str) -> list[tuple[int, str]]:
    if mime_type in TEXT_TYPES:
        return [(1, data.decode("utf-8"))]
    if mime_type == PDF_TYPE:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        pages = [(i + 1, (p.extract_text() or "").strip()) for i, p in enumerate(reader.pages)]
        pages = [(n, t) for n, t in pages if t]
        if not pages:
            raise ValidationFailedError("PDF has no extractable text (scanned documents need OCR, not supported)")
        return pages
    raise ValidationFailedError(f"Unsupported document type {mime_type}")
