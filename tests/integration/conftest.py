from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.enums import Classification, Role
from app.domains.identity.actor import Actor
from app.domains.identity.repository import UserRepository
from app.domains.identity.security import hash_password
from app.infrastructure.database.engine import Database

_PW_HASH = hash_password("Passw0rd!x")


@pytest.fixture
async def db(migrated_db: str) -> AsyncIterator[Database]:
    """Committed-data database for service tests; tables are emptied afterwards (audit_logs via DROP-safe TRUNCATE
    is blocked by its trigger, so the trigger is briefly disabled by the table owner, which tests run as)."""
    database = Database(create_async_engine(migrated_db))
    yield database
    async with database.engine.begin() as conn:
        await conn.execute(text("ALTER TABLE audit_logs DISABLE TRIGGER audit_logs_append_only"))
        await conn.execute(
            text(
                "TRUNCATE alerts, investigations, jobs, evidence, findings, reviews, reports, "
                "audit_logs, documents, llm_calls, investigation_events, risk_assessments, "
                "investigation_status_history, user_roles, users CASCADE"
            )
        )
        await conn.execute(text("ALTER TABLE audit_logs ENABLE TRIGGER audit_logs_append_only"))
    await database.dispose()


@pytest.fixture
def make_actor(db: Database) -> Any:
    async def _make(*roles: Role, clearance: Classification = Classification.CONFIDENTIAL) -> Actor:
        async with db.unit_of_work() as s:
            u = await UserRepository(s).create(
                email=f"{uuid.uuid4().hex[:10]}@bank.test",
                full_name="Test User",
                password_hash=_PW_HASH,
                roles=list(roles),
                clearance=clearance,
            )
        return Actor(user_id=u.id, roles=frozenset(roles), clearance=clearance, request_id="test")

    return _make
