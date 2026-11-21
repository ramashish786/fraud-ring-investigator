"""Seed a fresh environment with synthetic data (never real customer data).

    python -m app.seed --data /data/bank [--wipe-graph]

1. Users for each role (passwords from SEED_USER_PASSWORD; refused in prod).
2. Banking graph -> Neo4j (when GRAPH_BACKEND=neo4j).
3. Documents -> uploaded through DocumentService (the worker ingests them).
4. Alerts -> created through AlertService (idempotent on external_ref).

Every step is idempotent, so the seed can be re-run safely.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from app.core.container import Container
from app.core.enums import Classification, DocumentType, Role
from app.core.settings import get_settings
from app.domains.alerts.schemas import AlertCreate
from app.domains.alerts.service import AlertService
from app.domains.documents.schemas import DocumentMeta
from app.domains.documents.service import DocumentService
from app.domains.identity.actor import Actor
from app.domains.identity.repository import UserRepository
from app.domains.identity.security import hash_password
from app.infrastructure.observability.logging import configure_logging, get_logger

log = get_logger("seed")
USERS = [
    ("admin@bank.test", "Asha Admin", [Role.ADMIN], Classification.RESTRICTED),
    ("analyst@bank.test", "Arjun Analyst", [Role.ANALYST], Classification.CONFIDENTIAL),
    ("analyst2@bank.test", "Meera Analyst", [Role.ANALYST], Classification.INTERNAL),
    ("reviewer@bank.test", "Rahul Reviewer", [Role.REVIEWER], Classification.CONFIDENTIAL),
    ("auditor@bank.test", "Isha Auditor", [Role.AUDITOR], Classification.RESTRICTED),
]


async def seed_users(c: Container, password: str) -> dict[str, Actor]:
    actors = {}
    async with c.db.unit_of_work() as s:
        repo = UserRepository(s)
        for email, name, roles, clearance in USERS:
            user = await repo.get_by_email(email) or await repo.create(
                email=email, full_name=name, password_hash=hash_password(password), roles=roles, clearance=clearance
            )
            actors[email] = Actor(user_id=user.id, roles=frozenset(roles), clearance=clearance)
    log.info("seed.users", count=len(actors))
    return actors


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--data", default="data")
    p.add_argument("--wipe-graph", action="store_true")
    p.add_argument("--skip-graph", action="store_true")
    a = p.parse_args()
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)
    password = os.environ.get("SEED_USER_PASSWORD", "ChangeMe-123!")
    if settings.env == "prod":
        raise SystemExit("Refusing to seed synthetic users in prod")
    data = Path(a.data)
    c = Container.build(settings)
    try:
        actors = await seed_users(c, password)
        if settings.graph_backend == "neo4j" and not a.skip_graph:
            from app.graph.dataset import BankDataset
            from app.graph.loader import WIPE, load
            from app.graph.schema.bootstrap import apply_schema

            assert c.neo4j is not None
            await apply_schema(c.neo4j, settings.embedding_dimensions)
            if a.wipe_graph:
                await c.neo4j.run_schema(WIPE)
            counts = await load(c.neo4j, BankDataset.load(data))
            log.info("seed.graph", **counts)
        assert c.storage is not None
        docs = DocumentService(c.db, c.storage, settings.max_upload_bytes)
        for m in json.loads((data / "documents/manifest.json").read_text()):
            meta = DocumentMeta(
                title=m["title"],
                doc_type=DocumentType(m["doc_type"]),
                classification=Classification(m["classification"]),
                source=m["source"],
                case_id=m.get("case_id"),
            )
            await docs.upload(Path(m["path"]).name, (data / m["path"]).read_bytes(), meta, actors["admin@bank.test"])
        log.info("seed.documents_queued")
        alerts = AlertService(c.db)
        for raw in json.loads((data / "alerts/alerts.json").read_text()):
            await alerts.create(AlertCreate(**raw), actors["analyst@bank.test"])
        log.info("seed.done", users=[u[0] for u in USERS])
    finally:
        await c.aclose()


if __name__ == "__main__":
    asyncio.run(main())
