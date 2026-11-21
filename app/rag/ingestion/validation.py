"""Upload validation: never trust the client's filename or Content-Type."""

from __future__ import annotations

from pathlib import PurePath

from app.core.errors import ValidationFailedError

_EXT_MIME = {".md": "text/markdown", ".markdown": "text/markdown", ".txt": "text/plain", ".pdf": "application/pdf"}


def sniff_mime(filename: str, data: bytes, max_bytes: int) -> str:
    if not data:
        raise ValidationFailedError("Empty file")
    if len(data) > max_bytes:
        raise ValidationFailedError(f"File exceeds the {max_bytes // (1024 * 1024)} MB limit")
    ext = PurePath(filename).suffix.lower()
    mime = _EXT_MIME.get(ext)
    if not mime:
        raise ValidationFailedError(f"Unsupported file extension '{ext}'; allowed: {', '.join(sorted(_EXT_MIME))}")
    if mime == "application/pdf":
        if not data.startswith(b"%PDF-"):
            raise ValidationFailedError("File content is not a PDF")
        return mime
    if b"\x00" in data[:8192]:
        raise ValidationFailedError("Binary content in a text document")
    try:
        data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValidationFailedError("Text documents must be UTF-8") from exc
    return mime
