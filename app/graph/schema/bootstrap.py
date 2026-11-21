"""Neo4j constraints and indexes for the graph model (ARCHITECTURE §2.4, D-018) and the
RAG chunk indexes (D-009).

Run by the `migrate` service after `alembic upgrade head`:

    python -m app.graph.schema.bootstrap

Every statement is idempotent (`IF NOT EXISTS`).
"""

from __future__ import annotations

import asyncio
from typing import LiteralString

from neo4j.exceptions import Neo4jError

from app.core.settings import get_settings
from app.infrastructure.neo4j.client import Neo4jClient
from app.infrastructure.observability.logging import configure_logging, get_logger

log = get_logger("neo4j.bootstrap")

# (label, key property). A uniqueness constraint also creates the backing index.
NODE_KEYS: tuple[tuple[str, str], ...] = (
    ("Customer", "customer_id"),
    ("Account", "account_id"),
    ("Transaction", "txn_id"),
    ("Device", "device_id"),
    ("IPAddress", "ip"),
    ("Beneficiary", "beneficiary_id"),
    ("Merchant", "merchant_id"),
    ("FraudCase", "case_id"),
    ("Investigation", "investigation_id"),
    ("Document", "document_id"),
    ("Chunk", "chunk_id"),
)

RANGE_INDEXES: tuple[tuple[str, str, str], ...] = (
    ("transaction_ts", "Transaction", "ts"),
    ("ip_kind", "IPAddress", "kind"),
    ("fraud_case_status", "FraudCase", "status"),
    ("chunk_document", "Chunk", "document_id"),
)

FULLTEXT_INDEX: LiteralString = (
    "CREATE FULLTEXT INDEX chunk_text IF NOT EXISTS FOR (c:Chunk) ON EACH [c.text] "
    "OPTIONS {indexConfig: {`fulltext.analyzer`: 'english'}}"
)


def vector_index_statements(dimensions: int) -> tuple[LiteralString, LiteralString]:
    """(preferred, fallback). Preferred declares filterable properties for in-index filtering
    (Cypher 25, Neo4j 2026.01+); the fallback works on older 5.x servers (post-filtering, D-034)."""
    dims = int(dimensions)  # validated int from settings; DDL options can't be parameters
    cfg = f"OPTIONS {{indexConfig: {{`vector.dimensions`: {dims}, `vector.similarity_function`: 'cosine'}}}}"
    preferred = (
        f"CYPHER 25 CREATE VECTOR INDEX chunk_embedding IF NOT EXISTS FOR (c:Chunk) ON (c.embedding) "
        f"WITH [c.classification_rank, c.doc_group] {cfg}"
    )
    fallback = f"CREATE VECTOR INDEX chunk_embedding IF NOT EXISTS FOR (c:Chunk) ON (c.embedding) {cfg}"
    return preferred, fallback  # type: ignore[return-value,unused-ignore]


def schema_statements() -> list[LiteralString]:
    stmts: list[str] = [
        f"CREATE CONSTRAINT {label.lower()}_{prop}_unique IF NOT EXISTS FOR (n:{label}) REQUIRE n.{prop} IS UNIQUE"
        for label, prop in NODE_KEYS
    ]
    stmts += [f"CREATE INDEX {name} IF NOT EXISTS FOR (n:{label}) ON (n.{prop})" for name, label, prop in RANGE_INDEXES]
    stmts.append(FULLTEXT_INDEX)
    return stmts  # type: ignore[return-value,unused-ignore]


async def apply_schema(client: Neo4jClient, embedding_dimensions: int) -> None:
    for stmt in schema_statements():
        await client.run_schema(stmt)
    preferred, fallback = vector_index_statements(embedding_dimensions)
    try:
        await client.run_schema(preferred)
    except Neo4jError as exc:
        log.warning("neo4j.vector_index_fallback", reason=exc.code)
        await client.run_schema(fallback)
    log.info("neo4j.schema_applied", statements=len(schema_statements()) + 1)


async def wait_for_neo4j(client: Neo4jClient, attempts: int = 30) -> None:
    for attempt in range(1, attempts + 1):
        try:
            await client.ping(timeout_s=5)
            return
        except Exception as exc:
            log.info("neo4j.waiting", attempt=attempt, error_type=type(exc).__name__)
            await asyncio.sleep(2)
    raise SystemExit(f"Neo4j not reachable after {attempts} attempts")


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)
    client = Neo4jClient.from_settings(settings)
    try:
        await wait_for_neo4j(client)
        await apply_schema(client, settings.embedding_dimensions)
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
