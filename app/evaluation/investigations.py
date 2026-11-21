"""End-to-end evaluation of investigation quality (spec §14).

    python -m app.evaluation.investigations --data data --out reports [--recovery-trials 6]

Runs every generated alert through the real workflow (worker, checkpointing, evidence, LLM
tasks) using whatever the environment configures (GRAPH_BACKEND, LLM_PROVIDER). Human review
is simulated by a policy bot: approve when risk is medium or higher, otherwise reject.

Metrics: detection precision/recall/F1, ring discovery precision/recall, retrieval Recall@K,
evidence completeness, citation correctness, unsupported-claim rate, latency, recovery success.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import func, select, text

from app.agents.checkpoint import setup_checkpointer
from app.agents.deps import FaultInjector
from app.core.container import Container
from app.core.enums import Classification, InvestigationStatus, ReviewDecision, Role
from app.core.settings import Settings, get_settings
from app.domains.alerts.schemas import AlertCreate
from app.domains.alerts.service import AlertService
from app.domains.evidence.models import Evidence, Finding
from app.domains.identity.actor import Actor
from app.domains.identity.repository import UserRepository
from app.domains.identity.security import hash_password
from app.domains.investigations.schemas import ReviewCreate
from app.domains.investigations.service import InvestigationService
from app.evaluation.retrieval import evaluate as evaluate_retrieval
from app.evaluation.retrieval import ingest_corpus
from app.infrastructure.llm.embeddings import HashingEmbedder
from app.infrastructure.observability.logging import configure_logging
from workers.main import Worker, build_worker

EXPECTED_EVIDENCE: dict[str, list[set[str]]] = {  # per pattern: every group must have at least one kind present
    "spec_example": [
        {"structuring"},
        {"beneficiary_cluster"},
        {"new_beneficiary_transfer", "pass_through"},
        {"shared_device", "shared_ip"},
        {"prior_case"},
    ],
    "mule_network": [{"pass_through"}, {"shared_device", "shared_ip"}],
    "ato": [{"new_beneficiary_transfer"}, {"shared_device", "shared_ip"}],
    "synthetic_identity": [{"shared_device"}],
    "promo_abuse": [{"shared_device"}],
    "round_tripping": [{"value_cycle", "pass_through"}],
    "card_testing": [{"card_testing"}],
}
SECTION_KINDS = {
    "transaction_patterns": {
        "pass_through",
        "structuring",
        "card_testing",
        "flow_summary",
        "value_cycle",
        "new_beneficiary_transfer",
        "transaction",
    },
    "graph_relationships": {
        "shared_device",
        "shared_ip",
        "beneficiary",
        "money_flow",
        "prior_case",
        "beneficiary_cluster",
    },
    "historical_case_similarities": {"document_passage"},
}
FLAG_AT = {"medium", "high", "critical"}


def _prf(tp: int, fp: int, fn: int) -> dict[str, float]:
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    return {"precision": round(p, 3), "recall": round(r, 3), "f1": round(2 * p * r / (p + r), 3) if p + r else 0.0}


async def _drain(worker: Worker) -> None:
    while await worker.run_once():
        pass


async def _users(c: Container) -> tuple[Actor, Actor]:
    tag = uuid.uuid4().hex[:6]
    out = []
    async with c.db.unit_of_work() as s:
        for role in (Role.ANALYST, Role.REVIEWER):
            u = await UserRepository(s).create(
                email=f"eval-{role.value}-{tag}@bank.test",
                full_name=f"Eval {role}",
                password_hash=hash_password(uuid.uuid4().hex),
                roles=[role],
                clearance=Classification.CONFIDENTIAL,
            )
            out.append(Actor(user_id=u.id, roles=frozenset({role}), clearance=Classification.CONFIDENTIAL))
    return out[0], out[1]


async def _investigate(
    c: Container, worker: Worker, analyst: Actor, reviewer: Actor, alert: dict[str, Any], run_tag: str
) -> dict[str, Any]:
    body = {k: v for k, v in alert.items() if k != "truth"}
    a, _ = await AlertService(c.db).create(
        AlertCreate(**{**body, "external_ref": f"{body['external_ref']}-{run_tag}"}), analyst
    )
    svc = InvestigationService(c.db)
    inv, _ = await svc.start(a.id, analyst, None)
    t0 = time.perf_counter()
    await _drain(worker)
    latency = time.perf_counter() - t0
    detail = await svc.get(inv.id)
    if detail.status != InvestigationStatus.AWAITING_REVIEW or not detail.risk:
        return {"alert": alert, "investigation_id": str(inv.id), "status": detail.status.value, "latency_s": latency}
    category = detail.risk.category.value
    decision = ReviewDecision.APPROVED if category in FLAG_AT else ReviewDecision.REJECTED
    await svc.submit_review(inv.id, ReviewCreate(decision=decision, comment="evaluation policy bot"), reviewer)
    await _drain(worker)
    final = await svc.get(inv.id)
    if final.status != InvestigationStatus.COMPLETED:  # e.g. an injected failure parked the resume job
        return {"alert": alert, "investigation_id": str(inv.id), "status": final.status.value, "latency_s": latency}
    report = await svc.report(inv.id)
    async with c.db.read_session() as s:
        kinds = set((await s.scalars(select(Evidence.kind).where(Evidence.investigation_id == inv.id))).all())
        n_evidence = await s.scalar(
            select(func.count()).select_from(Evidence).where(Evidence.investigation_id == inv.id)
        )
        findings = (
            await s.execute(select(Finding.category, Finding.is_supported).where(Finding.investigation_id == inv.id))
        ).all()
    return {
        "alert": alert,
        "investigation_id": str(inv.id),
        "status": (await svc.get(inv.id)).status.value,
        "latency_s": latency,
        "score": detail.risk.score,
        "category": category,
        "kinds": kinds,
        "n_evidence": n_evidence,
        "findings": findings,
        "report": report.content,
    }


async def run(settings: Settings, data: Path, out: Path, recovery_trials: int) -> dict[str, Any]:
    await setup_checkpointer(settings.database_url.get_secret_value())
    c = Container.build(settings)
    assert c.chunks and c.embedder
    if settings.graph_backend == "memory":
        await ingest_corpus(data, c.chunks, c.embedder)
    analyst, reviewer = await _users(c)
    truth_by_ref = {t["external_ref"]: t for t in json.loads((data / "labels/alert_truth.json").read_text())}
    alerts = [
        {**a, "truth": truth_by_ref[a["external_ref"]]} for a in json.loads((data / "alerts/alerts.json").read_text())
    ]
    rings = {
        r["ring_id"]: set(r["actor_accounts"]) | set(r["victim_accounts"])
        for r in json.loads((data / "labels/rings.json").read_text())
    }
    spec = json.loads((data / "labels/spec_example.json").read_text())
    rings["RING-SPEC_EXAMPLE-1"] = rings.get("RING-SPEC_EXAMPLE-1", set()) | {spec["account_a"], *spec["beneficiaries"]}
    run_tag = uuid.uuid4().hex[:6]
    results: list[dict[str, Any]] = []
    async with build_worker(settings, c) as worker:
        for a in alerts:
            results.append(await _investigate(c, worker, analyst, reviewer, a, run_tag))

    # ------------------------------------------------------------- detection
    done = [r for r in results if "category" in r]
    tp = sum(1 for r in done if r["alert"]["truth"]["is_fraud"] and r["category"] in FLAG_AT)
    fp = sum(1 for r in done if not r["alert"]["truth"]["is_fraud"] and r["category"] in FLAG_AT)
    fn = sum(1 for r in done if r["alert"]["truth"]["is_fraud"] and r["category"] not in FLAG_AT)
    tp_h = sum(1 for r in done if r["alert"]["truth"]["is_fraud"] and r["category"] in {"high", "critical"})
    fp_h = sum(1 for r in done if not r["alert"]["truth"]["is_fraud"] and r["category"] in {"high", "critical"})
    fn_h = sum(1 for r in done if r["alert"]["truth"]["is_fraud"] and r["category"] not in {"high", "critical"})

    # ------------------------------------------------------------- ring discovery
    ring_p, ring_r = [], []
    for r in done:
        ring = rings.get(r["alert"]["truth"].get("ring_id") or "")
        if not ring:
            continue
        implicated = {e["account_id"] for e in r["report"]["narrative"]["suspicious_entities"]}
        implicated.discard(r["alert"]["subject_account_id"])
        others = ring - {r["alert"]["subject_account_id"]}
        if implicated:
            ring_p.append(len(implicated & ring) / len(implicated))
        if others:
            ring_r.append(len(implicated & others) / len(others))

    # ------------------------------------------------------------- evidence completeness
    completeness = []
    for r in done:
        exp = EXPECTED_EVIDENCE.get(r["alert"]["truth"]["pattern"])
        if exp:
            completeness.append(sum(1 for group in exp if group & r["kinds"]) / len(exp))

    # ------------------------------------------------------------- citations
    kind_of: dict[str, str] = {}
    cited_ok = cited_total = 0
    unsupported = total_claims = 0
    for r in done:
        kind_of = {e["ref"]: e["kind"] for e in r["report"]["evidence"]}
        for section, kinds in SECTION_KINDS.items():
            for claim in r["report"]["narrative"][section]:
                cited_total += 1
                cited_ok += any(kind_of.get(ref) in kinds for ref in claim["evidence_refs"])
        claims = [f for f in r["findings"] if f[0] != "missing_information"]
        unsupported += sum(1 for f in claims if not f[1]) + r["report"]["generation"]["unsupported_claims"]
        total_claims += len(claims) + sum(
            len(r["report"]["narrative"][k])
            for k in (
                "transaction_patterns",
                "graph_relationships",
                "supporting_evidence",
                "contradictions",
                "historical_case_similarities",
            )
        )

    # ------------------------------------------------------------- recovery
    recovery_nodes = ["synthesizer", "risk_assessment", "case_retriever", "planner", "report_generator", "intake"]
    clean = {r["alert"]["external_ref"]: r["n_evidence"] for r in done}
    recovered = 0
    trials = [a for a in alerts if a["external_ref"] in clean][:recovery_trials]
    for i, a in enumerate(trials):
        node = recovery_nodes[i % len(recovery_nodes)]
        async with build_worker(settings, c, FaultInjector(fail_once={node})) as fw:
            res = await _investigate(c, fw, analyst, reviewer, a, f"{run_tag}-r{i}")
            if res.get("status") != "completed":  # the injected failure parks the job: retry it now
                async with c.db.unit_of_work() as s:
                    await s.execute(
                        text("UPDATE jobs SET run_after = now() WHERE investigation_id = :i AND status = 'failed'"),
                        {"i": uuid.UUID(res["investigation_id"])},
                    )
                await _drain(fw)
                inv = await InvestigationService(c.db).get(uuid.UUID(res["investigation_id"]))
                if inv.status == InvestigationStatus.AWAITING_REVIEW:
                    await InvestigationService(c.db).submit_review(
                        inv.id, ReviewCreate(decision=ReviewDecision.REJECTED, comment="recovery trial"), reviewer
                    )
                    await _drain(fw)
                inv = await InvestigationService(c.db).get(uuid.UUID(res["investigation_id"]))
                async with c.db.read_session() as s:
                    n = await s.scalar(
                        select(func.count()).select_from(Evidence).where(Evidence.investigation_id == inv.id)
                    )
                ok = inv.status == InvestigationStatus.COMPLETED and n >= clean[a["external_ref"]] - 1
            else:
                ok = res.get("n_evidence", 0) >= clean[a["external_ref"]] - 1
            recovered += ok

    retrieval = await evaluate_retrieval(
        data, HashingEmbedder(settings.embedding_dimensions) if settings.llm_provider == "fake" else c.embedder
    )
    lat = sorted(r["latency_s"] for r in results)
    metrics = {
        "environment": {
            "graph_backend": settings.graph_backend,
            "llm_provider": settings.llm_provider,
            "alerts": len(alerts),
            "completed": len(done),
        },
        "detection_medium_plus": {**_prf(tp, fp, fn), "tp": tp, "fp": fp, "fn": fn},
        "detection_high_plus": {**_prf(tp_h, fp_h, fn_h), "tp": tp_h, "fp": fp_h, "fn": fn_h},
        "ring_discovery": {
            "precision": round(statistics.mean(ring_p), 3) if ring_p else None,
            "recall": round(statistics.mean(ring_r), 3) if ring_r else None,
            "alerts_scored": len(ring_p),
        },
        "retrieval": retrieval,
        "evidence_completeness": round(statistics.mean(completeness), 3) if completeness else None,
        "citation_correctness": round(cited_ok / cited_total, 3) if cited_total else None,
        "unsupported_claim_rate": round(unsupported / total_claims, 4) if total_claims else None,
        "latency_s": {
            "p50": round(lat[len(lat) // 2], 2),
            "p95": round(lat[int(len(lat) * 0.95) - 1], 2),
            "max": round(lat[-1], 2),
        },
        "workflow_recovery": {"trials": len(trials), "succeeded": recovered},
        "per_alert": [
            {
                "ref": r["alert"]["external_ref"],
                "pattern": r["alert"]["truth"]["pattern"],
                "is_fraud": r["alert"]["truth"]["is_fraud"],
                "score": r.get("score"),
                "category": r.get("category"),
                "status": r["status"],
            }
            for r in results
        ],
    }
    await c.aclose()
    out.mkdir(parents=True, exist_ok=True)
    (out / "evaluation.json").write_text(json.dumps(metrics, indent=2, default=str))
    (out / "evaluation.md").write_text(render(metrics))
    return metrics


def render(m: dict[str, Any]) -> str:
    e = m["environment"]
    lines = [
        "# Investigation evaluation",
        "",
        f"Backend: graph=`{e['graph_backend']}`, llm=`{e['llm_provider']}`. Alerts: {e['alerts']} "
        f"(completed {e['completed']}). Human review simulated (approve if risk ≥ medium).",
        "",
        "| Metric | Value |",
        "|---|---|",
    ]
    d, dh = m["detection_medium_plus"], m["detection_high_plus"]
    rows = [
        (
            "Detection precision / recall / F1 (flag at ≥ medium)",
            f"{d['precision']} / {d['recall']} / {d['f1']} (TP {d['tp']}, FP {d['fp']}, FN {d['fn']})",
        ),
        (
            "Detection precision / recall / F1 (flag at ≥ high)",
            f"{dh['precision']} / {dh['recall']} / {dh['f1']} (TP {dh['tp']}, FP {dh['fp']}, FN {dh['fn']})",
        ),
        (
            "Fraud ring discovery precision / recall",
            f"{m['ring_discovery']['precision']} / {m['ring_discovery']['recall']}",
        ),
        (
            "Retrieval Recall@5 (hybrid) / MRR",
            f"{m['retrieval']['hybrid']['recall@5']} / {m['retrieval']['hybrid']['mrr']}",
        ),
        ("Evidence completeness", m["evidence_completeness"]),
        ("Citation correctness (cited evidence matches the section)", m["citation_correctness"]),
        ("Unsupported claim rate", m["unsupported_claim_rate"]),
        (
            "Latency to review-ready p50 / p95 / max (s)",
            f"{m['latency_s']['p50']} / {m['latency_s']['p95']} / {m['latency_s']['max']}",
        ),
        (
            "Workflow recovery (injected failures)",
            f"{m['workflow_recovery']['succeeded']}/{m['workflow_recovery']['trials']}",
        ),
    ]
    lines += [f"| {k} | {v} |" for k, v in rows]
    lines += ["", "## Per alert", "", "| Alert | Pattern | Fraud? | Score | Category |", "|---|---|---|---|---|"]
    lines += [
        f"| {a['ref']} | {a['pattern']} | {'yes' if a['is_fraud'] else 'no'} | {a['score']} | {a['category']} |"
        for a in m["per_alert"]
    ]
    if e["llm_provider"] == "fake":
        lines += [
            "",
            "> With the fake LLM, citation correctness and unsupported-claim rate measure the pipeline's "
            "guardrails, not model quality. Re-run with `LLM_PROVIDER=openai` for model numbers.",
        ]
    return "\n".join(lines) + "\n"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--data", default="data")
    p.add_argument("--out", default="reports")
    p.add_argument("--recovery-trials", type=int, default=6)
    a = p.parse_args()
    settings = get_settings()
    if settings.graph_backend == "memory":
        # The in-memory graph must be built from the same dataset as the alerts and labels. Without this, a stale
        # MEMORY_DATASET_DIR silently scores alerts against a different bank (caught by the CI quality gate, D-053).
        settings = settings.model_copy(update={"memory_dataset_dir": str(a.data)})
    configure_logging("WARNING", settings.log_json)
    m = asyncio.run(run(settings, Path(a.data), Path(a.out), a.recovery_trials))
    print(render(m))


if __name__ == "__main__":
    main()
