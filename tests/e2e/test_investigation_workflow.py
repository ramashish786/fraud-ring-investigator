"""Complete investigations on known synthetic scenarios (spec §14 end-to-end)."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import func, select, text

from app.agents.deps import FaultInjector
from app.core.enums import InvestigationStatus, ReviewDecision, Role, Severity
from app.domains.alerts.schemas import AlertCreate
from app.domains.alerts.service import AlertService
from app.domains.audit.service import verify_chain
from app.domains.evidence.models import Evidence
from app.domains.evidence.service import EvidenceService
from app.domains.investigations.schemas import ReviewCreate
from app.domains.investigations.service import InvestigationService
from app.infrastructure.database.engine import Database

pytestmark = pytest.mark.integration


async def _start(db: Database, actor: Any, alert: dict[str, Any]) -> Any:
    a, _ = await AlertService(db).create(AlertCreate(**{k: v for k, v in alert.items() if k != "truth"}), actor)
    inv, _ = await InvestigationService(db).start(a.id, actor, None)
    return inv.id


async def _drain(worker: Any) -> None:
    while await worker.run_once():
        pass


async def test_spec_example_end_to_end(
    db: Database, make_actor: Any, worker_factory: Any, alerts: list[dict[str, Any]]
) -> None:
    analyst, reviewer = await make_actor(Role.ANALYST), await make_actor(Role.REVIEWER)
    spec_alert = next(a for a in alerts if a["truth"]["pattern"] == "spec_example")
    worker = await worker_factory()
    svc = InvestigationService(db)
    inv_id = await _start(db, analyst, spec_alert)
    await _drain(worker)

    detail = await svc.get(inv_id)
    assert detail.status == InvestigationStatus.AWAITING_REVIEW
    assert detail.risk and detail.risk.category in (Severity.HIGH, Severity.CRITICAL)
    fired = {f.rule_id for f in detail.risk.factors}
    assert {"R-BEN-02", "R-CASE-01", "R-STR-01"} <= fired
    assert all(f.evidence_refs for f in detail.risk.factors)

    await svc.submit_review(inv_id, ReviewCreate(decision=ReviewDecision.APPROVED, comment="Escalate"), reviewer)
    await _drain(worker)
    detail = await svc.get(inv_id)
    assert detail.status == InvestigationStatus.COMPLETED and detail.alert.status == "closed"

    report = await svc.report(inv_id)
    c = report.content
    assert c["risk_assessment"]["score"] == detail.risk.score  # report can't change the deterministic score
    assert c["generation"]["unsupported_claims"] == 0
    known = {e["ref"] for e in c["evidence"]}
    cited = {
        r
        for k in ("supporting_evidence", "contradictions", "graph_relationships")
        for cl in c["narrative"][k]
        for r in cl["evidence_refs"]
    }
    assert cited and cited <= known  # no fabricated citations
    assert any("inconclusive" in cl["statement"] for cl in c["narrative"]["contradictions"])
    assert "## Evidence appendix" in report.markdown and "Confirmed outcome" in report.markdown

    levels = {
        e.assertion_level
        for e in (await EvidenceService(db).list_page(inv_id, analyst, levels=None, limit=500, offset=0)).items
    }
    assert {"verified_fact", "observed_relationship", "inferred_pattern", "confirmed_outcome"} <= levels
    timeline = await svc.timeline(inv_id)
    nodes = {e.label.split(":")[0] for e in timeline if e.kind == "node"}
    assert {"intake", "planner", "graph_investigator", "case_retriever", "synthesizer", "report_generator"} <= nodes
    async with db.read_session() as s:
        n, broken = await verify_chain(s)
        llm_calls = await s.scalar(text("SELECT count(*) FROM llm_calls WHERE investigation_id = :i"), {"i": inv_id})
    assert broken is None and n >= 4
    assert llm_calls >= 5  # planner, case, policy, synthesizer, report
    from prometheus_client import REGISTRY

    assert (REGISTRY.get_sample_value("fri_investigation_time_to_review_seconds_count") or 0) >= 1
    assert (
        REGISTRY.get_sample_value(
            "fri_workflow_node_duration_seconds_count", {"node": "graph_investigator", "status": "completed"}
        )
        or 0
    ) >= 1
    assert (REGISTRY.get_sample_value("fri_llm_calls_total", {"task": "synthesizer", "status": "ok"}) or 0) >= 1
    assert (
        REGISTRY.get_sample_value("fri_jobs_processed_total", {"kind": "resume_investigation", "status": "succeeded"})
        or 0
    ) >= 1
    assert (REGISTRY.get_sample_value("fri_investigations_finished_total", {"outcome": "completed"}) or 0) >= 1


async def test_queue_metrics_reflect_waiting_jobs(
    db: Database, make_actor: Any, worker_factory: Any, alerts: list[dict[str, Any]]
) -> None:
    from prometheus_client import REGISTRY

    analyst = await make_actor(Role.ANALYST)
    worker = await worker_factory()
    await _start(db, analyst, alerts[1])
    await worker.refresh_queue_metrics()
    assert REGISTRY.get_sample_value("fri_job_queue_depth", {"kind": "start_investigation"}) == 1
    assert (REGISTRY.get_sample_value("fri_job_queue_oldest_age_seconds") or 0) >= 0
    await _drain(worker)
    await worker.refresh_queue_metrics()
    assert REGISTRY.get_sample_value("fri_job_queue_depth", {"kind": "start_investigation"}) == 0


async def test_household_false_positive_scores_low(
    db: Database, make_actor: Any, worker_factory: Any, alerts: list[dict[str, Any]]
) -> None:
    analyst = await make_actor(Role.ANALYST)
    benign = next(a for a in alerts if a["truth"]["pattern"] == "household_shared_device")
    worker = await worker_factory()
    inv_id = await _start(db, analyst, benign)
    await _drain(worker)
    detail = await InvestigationService(db).get(inv_id)
    assert detail.status == InvestigationStatus.AWAITING_REVIEW
    assert detail.risk and detail.risk.category == Severity.LOW


async def test_crash_mid_workflow_recovers_without_duplicate_evidence(
    db: Database, make_actor: Any, worker_factory: Any, alerts: list[dict[str, Any]]
) -> None:
    analyst = await make_actor(Role.ANALYST)
    alert = next(a for a in alerts if a["truth"]["pattern"] == "mule_network")
    worker = await worker_factory(FaultInjector(fail_once={"synthesizer"}))
    inv_id = await _start(db, analyst, alert)
    assert await worker.run_once()  # first attempt dies in the synthesizer
    async with db.read_session() as s:
        job_status = await s.scalar(text("SELECT status FROM jobs WHERE investigation_id = :i"), {"i": inv_id})
        before = await s.scalar(select(func.count()).select_from(Evidence).where(Evidence.investigation_id == inv_id))
    assert job_status == "failed" and before > 0  # investigator work was persisted before the crash
    async with db.unit_of_work() as s:
        await s.execute(text("UPDATE jobs SET run_after = now() WHERE investigation_id = :i"), {"i": inv_id})
    await _drain(worker)
    detail = await InvestigationService(db).get(inv_id)
    assert detail.status == InvestigationStatus.AWAITING_REVIEW
    async with db.read_session() as s:
        after = await s.scalar(select(func.count()).select_from(Evidence).where(Evidence.investigation_id == inv_id))
        ran = (
            await s.execute(
                text(
                    "SELECT node, count(*) FROM investigation_events WHERE investigation_id = :i "
                    "AND status = 'completed' GROUP BY node"
                ),
                {"i": inv_id},
            )
        ).all()
    assert after == before  # resumed from checkpoint: investigators were not re-run
    assert dict(ran)["graph_investigator"] == 1


async def test_needs_more_work_loops_once_then_completes(
    db: Database, make_actor: Any, worker_factory: Any, alerts: list[dict[str, Any]]
) -> None:
    analyst, reviewer = await make_actor(Role.ANALYST), await make_actor(Role.REVIEWER)
    alert = next(a for a in alerts if a["truth"]["pattern"] == "ato")
    worker = await worker_factory()
    svc = InvestigationService(db)
    inv_id = await _start(db, analyst, alert)
    await _drain(worker)
    first_round = (await svc.get(inv_id)).round
    await svc.submit_review(
        inv_id, ReviewCreate(decision=ReviewDecision.NEEDS_MORE_WORK, comment="trace funds"), reviewer
    )
    await _drain(worker)
    detail = await svc.get(inv_id)
    assert detail.status == InvestigationStatus.AWAITING_REVIEW and detail.round == first_round + 1 <= 2
    await svc.submit_review(inv_id, ReviewCreate(decision=ReviewDecision.REJECTED, comment="explained"), reviewer)
    await _drain(worker)
    detail = await svc.get(inv_id)
    assert detail.status == InvestigationStatus.COMPLETED
    report = await svc.report(inv_id)
    assert report.content["human_review"]["decision"] == "rejected" and report.content["rounds"] == first_round + 1
