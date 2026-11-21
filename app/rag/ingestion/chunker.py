"""Structure-aware chunking.

Splits on Markdown headings first (a chunk never spans two sections, so its `section`
citation is accurate), then packs paragraphs up to `max_chars` with `overlap` characters
carried over to keep context across boundaries. A single overlong paragraph is split on
sentence boundaries.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_HEADING = re.compile(r"^(#{1,6})\s+(.+)$", re.M)
_SENTENCE = re.compile(r"(?<=[.!?])\s+")
ACCOUNT_ID = re.compile(r"\bA\d{6}\b")


@dataclass(frozen=True)
class TextChunk:
    page: int
    section: str | None
    text: str


def _sections(text: str) -> list[tuple[str | None, str]]:
    parts: list[tuple[str | None, str]] = []
    matches = list(_HEADING.finditer(text))
    if not matches or matches[0].start() > 0:
        parts.append((None, text[: matches[0].start()] if matches else text))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        parts.append((m.group(2).strip(), text[m.end() : end]))
    return [(h, b.strip()) for h, b in parts if b.strip()]


def _pieces(body: str, max_chars: int) -> list[str]:
    out: list[str] = []
    for para in re.split(r"\n\s*\n", body):
        para = para.strip()
        if len(para) <= max_chars:
            out.append(para)
            continue
        buf = ""
        for sent in _SENTENCE.split(para):
            if buf and len(buf) + len(sent) + 1 > max_chars:
                out.append(buf)
                buf = ""
            buf = f"{buf} {sent}".strip()
        if buf:
            out.append(buf)
    return [p for p in out if p]


def chunk_pages(pages: list[tuple[int, str]], *, max_chars: int = 900, overlap: int = 150) -> list[TextChunk]:
    chunks: list[TextChunk] = []
    for page, text in pages:
        for heading, body in _sections(text):
            buf = ""
            for piece in _pieces(body, max_chars):
                if buf and len(buf) + len(piece) + 2 > max_chars:
                    chunks.append(TextChunk(page, heading, buf))
                    buf = buf[-overlap:].split(" ", 1)[-1] if overlap else ""
                buf = f"{buf}\n\n{piece}".strip()
            if buf:
                chunks.append(TextChunk(page, heading, buf))
    return chunks
