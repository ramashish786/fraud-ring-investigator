"""Embedding providers behind one interface (D-010, D-037).

* OpenAIEmbedder: LangChain OpenAIEmbeddings with explicit `dimensions`, so the vector
  index size is a setting rather than an accident of the model.
* HashingEmbedder: deterministic feature-hashed bag of words and bigrams, L2-normalised.
  No network or key. Retrieval quality is lexical, which is enough to make tests and
  offline evaluation meaningful, but its numbers are not a proxy for OpenAI quality.

Switching provider changes the vector space: documents must be re-ingested.
"""

from __future__ import annotations

import asyncio
import hashlib
import itertools
import math
import re
import time
from typing import Protocol

from app.core.settings import Settings
from app.infrastructure.observability.logging import get_logger

log = get_logger("llm.embeddings")
_TOKEN = re.compile(r"[a-z0-9]+")
_STOP = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "in",
        "is",
        "it",
        "of",
        "on",
        "or",
        "that",
        "the",
        "this",
        "to",
        "was",
        "were",
        "with",
    ]
)


class Embedder(Protocol):
    name: str
    dimensions: int

    async def embed_documents(self, texts: list[str]) -> list[list[float]]: ...
    async def embed_query(self, text: str) -> list[float]: ...


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if t not in _STOP]


class HashingEmbedder:
    name = "hashing-bow"

    def __init__(self, dimensions: int) -> None:
        self.dimensions = dimensions

    def _embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dimensions
        toks = tokenize(text)
        feats = toks + [f"{a}_{b}" for a, b in itertools.pairwise(toks)]
        for f in feats:
            h = int.from_bytes(hashlib.blake2b(f.encode(), digest_size=8).digest(), "big")
            vec[h % self.dimensions] += 1.0 if (h >> 63) & 1 else -1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(t) for t in texts]

    async def embed_query(self, text: str) -> list[float]:
        return self._embed(text)


class OpenAIEmbedder:
    name = "openai"

    def __init__(self, settings: Settings) -> None:
        from langchain_openai import OpenAIEmbeddings

        assert settings.openai_api_key and settings.openai_embedding_model
        self.dimensions = settings.embedding_dimensions
        self.model = settings.openai_embedding_model
        self._client = OpenAIEmbeddings(
            model=self.model,
            dimensions=self.dimensions,
            openai_api_key=settings.openai_api_key,
            request_timeout=settings.llm_timeout_s,
            max_retries=settings.llm_max_retries,
        )
        self._sem = asyncio.Semaphore(settings.llm_max_concurrency)

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        async with self._sem:
            start = time.perf_counter()
            out = await self._client.aembed_documents(texts)
            log.info(
                "embeddings.batch",
                model=self.model,
                n=len(texts),
                latency_ms=round((time.perf_counter() - start) * 1000),
            )
            return out

    async def embed_query(self, text: str) -> list[float]:
        async with self._sem:
            return await self._client.aembed_query(text)


def build_embedder(settings: Settings) -> Embedder:
    return (
        OpenAIEmbedder(settings)
        if settings.llm_provider == "openai"
        else HashingEmbedder(settings.embedding_dimensions)
    )
