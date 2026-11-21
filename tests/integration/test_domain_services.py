"""Domain services against real PostgreSQL (no graph, no LLM)."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from sqlalchemy import select, text

from app.core.enums import (
    AlertType,
    AssertionLevel,
    EvidenceSource,
    InvestigationStatus,
    JobKind,
    ReviewDecision,
    Role,
    Severity,
)
from app.core.errors import ConflictError, PermissionDeniedError
from app.domains.alerts.schemas import AlertCreate
from app.domains.alerts.service import AlertService
from app.domains.audit.service import verify_chain
from app.domains.evidence.repository import EvidenceRepository
from app.domains.evidence.schemas import EvidenceItem
from app.domains.investigations.models import Investigation
from app.domains.investigations.repository import InvestigationRepository
from app.domains.investigations.schemas import ReviewCreate
from app.domains.investigations.service import InvestigationService
from app.infrastructure.database.engine import Database
from app.infrastructure.jobs.repository import JobRepository

pytestmark = pytest.mark.integration


def _alert(ref: str = "MON-1") -> AlertCreate:
    return AlertCreate(
        external_ref=ref,
        alert_type=AlertType.RAPID_PASS_THROUGH,
        severity=Severity.HIGH,
        subject_account_id="A000001",
        summary="test alert",
    )


async def test_alert_creation_is_idempotent_and_audited(db: Database, make_actor: Any) -> None:
    analyst = await make_actor(Role.ANALYST)
    svc = AlertService(db)
    a1, created1 = await svc.create(_alert(), analyst)
    a2, created2 = await svc.create(_alert(), analyst)
    assert created1 and not created2 and a1.id == a2.id
    async with db.read_session() as s:
        n, broken = await verify_chain(s)
    assert n == 1 and broken is None


async def test_concurrent_duplicate_alerts_create_one_row(db: Database, make_actor: Any) -> None:
    analyst = await make_actor(Role.ANALYST)
    results = await asyncio.gather(*(AlertService(db).create(_alert("MON-RACE"), analyst) for _ in range(5)))
    assert len({r.id for r, _ in results}) == 1
    assert sum(created for _, created in results) == 1


async def test_start_investigation_writes_job_atomically_and_is_idempotent(db: Database, make_actor: Any) -> None:
    analyst = await make_actor(Role.ANALYST)
    alert, _ = await AlertService(db).create(_alert(), analyst)
    svc = InvestigationService(db)
    inv, created = await svc.start(alert.id, analyst, "key-1")
    again, created_again = await svc.start(alert.id, analyst, "key-1")
    assert created and not created_again and again.id == inv.id
    with pytest.raises(ConflictError):  # different key, same alert, still active
        await svc.start(alert.id, analyst, "key-2")
    async with db.read_session() as s:
        jobs = (await s.execute(text("SELECT kind, investigation_id FROM jobs"))).all()
    assert jobs == [("start_investigation", inv.id)]


async def test_reviewer_rules(db: Database, make_actor: Any) -> None:
    analyst = await make_actor(Role.ANALYST, Role.REVIEWER)  # can start; must not review their own
    reviewer = await make_actor(Role.REVIEWER)
    alert, _ = await AlertService(db).create(_alert(), analyst)
    svc = InvestigationService(db)
    inv, _ = await svc.start(alert.id, analyst, None)
    with pytest.raises(ConflictError):  # not awaiting review yet
        await svc.submit_review(inv.id, ReviewCreate(decision=ReviewDecision.APPROVED), reviewer)
    async with db.unit_of_work() as s:
        repo = InvestigationRepository(s)
        row = await repo.get(inv.id)
        assert row
        await repo.transition(row, InvestigationStatus.RUNNING, actor_type="system")  # type: ignore[arg-type]
        await repo.transition(row, InvestigationStatus.AWAITING_REVIEW, actor_type="system")  # type: ignore[arg-type]
    with pytest.raises(PermissionDeniedError, match="cannot review"):
        await svc.submit_review(inv.id, ReviewCreate(decision=ReviewDecision.APPROVED), analyst)
    await svc.submit_review(inv.id, ReviewCreate(decision=ReviewDecision.APPROVED, comment="ok"), reviewer)
    with pytest.raises(ConflictError, match="already been submitted"):
        await svc.submit_review(inv.id, ReviewCreate(decision=ReviewDecision.REJECTED), reviewer)
    timeline = await svc.timeline(inv.id)
    assert [e.kind for e in timeline].count("review") == 1


async def test_invalid_transition_rejected(db: Database, make_actor: Any) -> None:
    analyst = await make_actor(Role.ANALYST)
    alert, _ = await AlertService(db).create(_alert(), analyst)
    inv, _ = await InvestigationService(db).start(alert.id, analyst, None)
    async with db.unit_of_work() as s:
        row = await InvestigationRepository(s).get(inv.id)
        assert row
        with pytest.raises(ConflictError):
            await InvestigationRepository(s).transition(row, InvestigationStatus.COMPLETED, actor_type="system")  # type: ignore[arg-type]


async def test_job_claim_is_exclusive_and_retries_with_backoff(db: Database, make_actor: Any) -> None:
    analyst = await make_actor(Role.ANALYST)
    alert, _ = await AlertService(db).create(_alert(), analyst)
    inv, _ = await InvestigationService(db).start(alert.id, analyst, None)

    async def claim(worker: str) -> Any:
        async with db.unit_of_work() as s:
            return await JobRepository(s).claim(worker, lease_s=60)

    claims = await asyncio.gather(*(claim(f"w{i}") for i in range(5)))
    got = [c for c in claims if c]
    assert len(got) == 1 and got[0].investigation_id == inv.id and got[0].kind == JobKind.START_INVESTIGATION
    async with db.unit_of_work() as s:
        status = await JobRepository(s).fail(got[0], "boom")
    assert status == "failed"
    assert await claim("w9") is None  # backoff: not runnable yet
    async with db.unit_of_work() as s:  # fast-forward the backoff
        await s.execute(text("UPDATE jobs SET run_after = now() - interval '1 second'"))
    again = await claim("w9")
    assert again and again.attempts == 2


async def test_expired_lease_makes_job_claimable(db: Database, make_actor: Any) -> None:
    analyst = await make_actor(Role.ANALYST)
    alert, _ = await AlertService(db).create(_alert(), analyst)
    await InvestigationService(db).start(alert.id, analyst, None)
    async with db.unit_of_work() as s:
        first = await JobRepository(s).claim("crashed-worker", lease_s=60)
        await s.execute(text("UPDATE jobs SET leased_until = now() - interval '1 second'"))
    async with db.unit_of_work() as s:
        second = await JobRepository(s).claim("healthy-worker", lease_s=60)
    assert first and second and first.id == second.id and second.attempts == 2


async def test_evidence_idempotent_refs_and_findings_replace(db: Database, make_actor: Any) -> None:
    analyst = await make_actor(Role.ANALYST)
    alert, _ = await AlertService(db).create(_alert(), analyst)
    inv, _ = await InvestigationService(db).start(alert.id, analyst, None)
    item = EvidenceItem(
        kind="transaction",
        assertion_level=AssertionLevel.VERIFIED_FACT,
        source=EvidenceSource.NEO4J,
        source_ref={"txn_id": "T1"},
        snapshot={"amount": 10},
        summary="T1 moved 10",
    )
    from app.domains.evidence.schemas import FindingIn

    async with db.unit_of_work() as s:
        repo = EvidenceRepository(s)
        refs1 = await repo.add_many(inv.id, [item], "transaction_investigator")
        refs2 = await repo.add_many(inv.id, [item], "transaction_investigator")
        assert refs1 == refs2 == [item.ref]
        for _ in range(2):  # node re-executed
            await repo.replace_findings(
                inv.id,
                "synthesizer:r0",
                [
                    FindingIn(
                        statement="supported",
                        category="supporting",
                        assertion_level=AssertionLevel.HYPOTHESIS,
                        evidence_refs=[item.ref],
                    ),
                    FindingIn(
                        statement="cites nothing real",
                        category="supporting",
                        assertion_level=AssertionLevel.HYPOTHESIS,
                        evidence_refs=["E-0000000000"],
                    ),
                ],
            )
    async with db.read_session() as s:
        _rows, total = await EvidenceRepository(s).list_page(inv.id)
        findings = await EvidenceRepository(s).findings(inv.id)
        _ = (await s.scalars(select(Investigation))).all()
    assert total == 1
    assert [(f.statement, f.is_supported, refs) for f, refs in findings] == [
        ("supported", True, [item.ref]),
        ("cites nothing real", False, []),
    ]
