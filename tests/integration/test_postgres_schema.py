"""PostgreSQL integration: migrations and database-enforced invariants."""

import os
import uuid

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.enums import AlertType, AssertionLevel, EvidenceSource, InvestigationStatus, Severity
from app.domains.alerts.models import Alert
from app.domains.evidence.models import Evidence
from app.domains.identity.models import User
from app.domains.investigations.models import Investigation

pytestmark = pytest.mark.integration


async def _tables(url: str) -> set[str]:
    engine = create_async_engine(url)
    async with engine.connect() as c:
        rows = await c.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public'"))
        names = {r[0] for r in rows}
    await engine.dispose()
    return names - {"alembic_version", "checkpoints", "checkpoint_blobs", "checkpoint_writes", "checkpoint_migrations"}


def test_migration_round_trip(migrated_db: str) -> None:
    import asyncio

    cfg = Config("alembic.ini")
    os.environ["DATABASE_URL"] = migrated_db
    assert len(asyncio.run(_tables(migrated_db))) == 17
    command.downgrade(cfg, "base")
    assert asyncio.run(_tables(migrated_db)) == set()
    command.upgrade(cfg, "head")
    assert len(asyncio.run(_tables(migrated_db))) == 17
    command.check(cfg)  # raises if models and migrations have drifted


async def _user_alert(s: AsyncSession) -> tuple[User, Alert]:
    user = User(email=f"{uuid.uuid4().hex[:8]}@bank.test", full_name="A Analyst", password_hash="x")
    s.add(user)
    await s.flush()
    alert = Alert(
        external_ref=uuid.uuid4().hex,
        alert_type=AlertType.RAPID_PASS_THROUGH,
        severity=Severity.HIGH,
        subject_account_id="A000001",
        summary="test",
        created_by=user.id,
    )
    s.add(alert)
    await s.flush()
    return user, alert


async def test_roles_are_seeded(db_session: AsyncSession) -> None:
    roles = (await db_session.execute(text("SELECT name FROM roles ORDER BY name"))).scalars().all()
    assert roles == ["admin", "analyst", "auditor", "reviewer"]


async def test_only_one_active_investigation_per_alert(db_session: AsyncSession) -> None:
    user, alert = await _user_alert(db_session)
    db_session.add(Investigation(alert_id=alert.id, created_by=user.id))
    await db_session.flush()
    nested = await db_session.begin_nested()
    db_session.add(Investigation(alert_id=alert.id, created_by=user.id))
    with pytest.raises(IntegrityError, match="uq_investigations_alert_active"):
        await db_session.flush()
    await nested.rollback()
    # a *completed* investigation does not block a new one
    db_session.add(Investigation(alert_id=alert.id, created_by=user.id, status=InvestigationStatus.COMPLETED))
    await db_session.flush()


async def test_llm_evidence_must_be_hypothesis(db_session: AsyncSession) -> None:
    user, alert = await _user_alert(db_session)
    inv = Investigation(alert_id=alert.id, created_by=user.id)
    db_session.add(inv)
    await db_session.flush()
    db_session.add(
        Evidence(
            investigation_id=inv.id,
            ref="E-0001",
            kind="claim",
            source=EvidenceSource.LLM,
            assertion_level=AssertionLevel.VERIFIED_FACT,
            source_ref={},
            snapshot={},
            summary="LLM claims a fact",
            content_hash="a" * 64,
            produced_by_node="synthesizer",
        )
    )
    with pytest.raises(IntegrityError, match="ck_evidence_llm_only_hypothesis"):
        await db_session.flush()


async def test_evidence_writes_are_idempotent_by_content_hash(db_session: AsyncSession) -> None:
    user, alert = await _user_alert(db_session)
    inv = Investigation(alert_id=alert.id, created_by=user.id)
    db_session.add(inv)
    await db_session.flush()
    stmt = text("""INSERT INTO evidence (investigation_id, ref, kind, assertion_level, source, source_ref, snapshot,
                   summary, content_hash, produced_by_node)
                   VALUES (:i, :ref, 'transaction', 'verified_fact', 'neo4j', '{}', '{}', 's', :h, 'txn')
                   ON CONFLICT (investigation_id, content_hash) DO NOTHING""")
    await db_session.execute(stmt, {"i": inv.id, "ref": "E-0001", "h": "b" * 64})
    await db_session.execute(stmt, {"i": inv.id, "ref": "E-0002", "h": "b" * 64})  # re-run of the same node
    n = await db_session.scalar(text("SELECT count(*) FROM evidence WHERE investigation_id = :i"), {"i": inv.id})
    assert n == 1


async def test_audit_log_is_append_only(db_session: AsyncSession) -> None:
    await db_session.execute(
        text("""INSERT INTO audit_logs (actor_type, action, entity_type, entity_id, hash)
                                     VALUES ('system', 'test', 'x', '1', :h)"""),
        {"h": uuid.uuid4().hex * 2},
    )
    nested = await db_session.begin_nested()
    with pytest.raises(DBAPIError, match="append-only"):
        await db_session.execute(text("UPDATE audit_logs SET action = 'tampered'"))
    await nested.rollback()
    with pytest.raises(DBAPIError, match="append-only"):
        await db_session.execute(text("DELETE FROM audit_logs"))


async def test_optimistic_locking_on_investigations(migrated_db: str) -> None:
    from sqlalchemy.orm.exc import StaleDataError

    from app.infrastructure.database.engine import Database

    db = Database(create_async_engine(migrated_db))
    async with db.unit_of_work() as s:
        user, alert = await _user_alert(s)
        inv = Investigation(alert_id=alert.id, created_by=user.id)
        s.add(inv)
    inv_id = inv.id
    async with db.read_session() as a, db.read_session() as b:
        ia, ib = await a.get(Investigation, inv_id), await b.get(Investigation, inv_id)
        assert ia and ib
        ia.status = InvestigationStatus.RUNNING
        await a.commit()
        ib.status = InvestigationStatus.CANCELLED  # stale copy: must not silently overwrite
        with pytest.raises(StaleDataError):
            await b.commit()
    await db.dispose()
