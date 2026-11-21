"""Process-wide resources (composition root, D-025).

Built once at startup by the API lifespan or the worker, closed at shutdown. Routes and
nodes get these through dependencies, never module globals, so tests inject fakes.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import app.infrastructure.database.registry  # noqa: F401  (register every ORM model before first use)
from app.core.settings import Settings
from app.graph.repository import GraphRepository
from app.infrastructure.database.engine import Database
from app.infrastructure.llm.embeddings import Embedder, build_embedder
from app.infrastructure.llm.service import LLMService, build_llm
from app.infrastructure.neo4j.client import Neo4jClient
from app.infrastructure.storage import LocalFileStorage
from app.rag.retrieval.hybrid import HybridSearch
from app.rag.store import ChunkStore


@dataclass
class Container:
    settings: Settings
    db: Database
    neo4j: Neo4jClient | None
    graph: GraphRepository | None = None
    chunks: ChunkStore | None = None
    embedder: Embedder | None = None
    llm: LLMService | None = None
    storage: LocalFileStorage | None = None
    extras: dict[str, object] = field(default_factory=dict)

    @classmethod
    def build(cls, settings: Settings) -> Container:
        db = Database.from_settings(settings)
        neo4j: Neo4jClient | None = None
        graph: GraphRepository
        chunks: ChunkStore
        if settings.graph_backend == "neo4j":
            from app.graph.neo4j_repository import Neo4jGraphRepository
            from app.rag.store import Neo4jChunkStore

            neo4j = Neo4jClient.from_settings(settings)
            graph, chunks = Neo4jGraphRepository(neo4j), Neo4jChunkStore(neo4j)
        else:  # tests / offline evaluation only (D-026)
            from app.graph.dataset import BankDataset
            from app.graph.memory_repository import InMemoryGraphRepository
            from app.rag.store import InMemoryChunkStore

            graph, chunks = InMemoryGraphRepository(BankDataset.load(settings.memory_dataset_dir)), InMemoryChunkStore()
        return cls(
            settings=settings,
            db=db,
            neo4j=neo4j,
            graph=graph,
            chunks=chunks,
            embedder=build_embedder(settings),
            llm=build_llm(settings, db),
            storage=LocalFileStorage(settings.document_storage_dir),
        )

    @property
    def search(self) -> HybridSearch:
        assert self.chunks is not None and self.embedder is not None
        return HybridSearch(self.chunks, self.embedder)

    async def aclose(self) -> None:
        try:
            if self.neo4j is not None:
                await self.neo4j.close()
        finally:
            await self.db.dispose()
